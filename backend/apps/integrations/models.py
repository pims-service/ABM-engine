"""Integration settings: one row per provider connection of a client (issue #76, Brief section 17).

A client can connect any number of providers per category (an LLM, a company-data provider, a
CRM, ...). A row holds the non-secret ``config`` in plain JSON and the secrets in
``credentials``, which is encrypted at rest (``apps.integrations.crypto``) and write-only through
the API. Adapters (issues #74, #82 to #85) read credentials only through
``apps.integrations.services.get_credentials``.

Change connections through ``apps.integrations.services`` so every change is audited, without
secret values. Rows are archived, never deleted.

Usage, budgets, cache and raw payloads (issues #79 and #80,
``docs/integrations/usage-and-cache.md``) are further down: ``ProviderCall`` (append-only, one
row per call, no payloads), ``UsageBudget``, ``CachedResponse`` and ``RawPayload`` (the last two
are removable by retention).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    ArchivableModel,
    ArchivableQuerySet,
    BaseModel,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)

from .fields import EncryptedCredentialsField

PROVIDER_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
PROVIDER_KEY_RE = re.compile(PROVIDER_KEY_PATTERN)
MAX_TEST_MESSAGE_LENGTH = 500


class IntegrationCategory(models.TextChoices):
    """The kinds of external service the platform talks to (Brief section 17)."""

    LLM = "llm", "LLM"
    COMPANY_DATA = "company_data", "Company data"
    PEOPLE_DATA = "people_data", "People data"
    EMAIL_ENRICHMENT = "email_enrichment", "Email enrichment"
    EMAIL_VERIFICATION = "email_verification", "Email verification"
    CRM = "crm", "CRM"
    OUTREACH = "outreach", "Outreach"
    STORAGE = "storage", "Storage"
    AUTOMATION = "automation", "Automation"


class ConnectionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    ERROR = "error", "Error"


class ConnectionTestStatus(models.TextChoices):
    """Outcome of the last connection test. Blank = never tested."""

    OK = "ok", "OK"
    FAILED = "failed", "Failed"


def _in(field: str, choices: type[models.TextChoices], *, blank: bool = False) -> models.Q:
    values = list(choices.values) + ([""] if blank else [])
    return models.Q(**{f"{field}__in": values})


class IntegrationConnectionQuerySet(  # type: ignore[override]
    ArchivableQuerySet["IntegrationConnection"], TenantQuerySet["IntegrationConnection"]
):
    def usable(self) -> IntegrationConnectionQuerySet:
        """Active, non-archived connections: the ones an adapter may use."""
        return self.active().filter(status=ConnectionStatus.ACTIVE)


class IntegrationConnection(ArchivableModel, TenantModel, BaseModel):
    """A client's connection to one provider. ``credentials`` is write-only (see module doc)."""

    category = models.CharField(max_length=32, choices=IntegrationCategory.choices)
    provider = models.CharField(
        max_length=64,
        validators=[
            RegexValidator(
                PROVIDER_KEY_PATTERN,
                "Use lower-case letters, digits and underscores.",
            )
        ],
        help_text="Provider key, e.g. 'openai' or 'fake'.",
    )
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=ConnectionStatus.choices, default=ConnectionStatus.ACTIVE
    )
    config = models.JSONField(
        default=dict, blank=True, help_text="Non-secret settings. Never put a secret here."
    )
    credentials = EncryptedCredentialsField()
    credentials_hint = models.JSONField(
        default=dict,
        blank=True,
        help_text="Masked hint per credential name, e.g. ****abcd. Not a secret.",
    )
    credentials_updated_at = models.DateTimeField(null=True, blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(
        max_length=16, choices=ConnectionTestStatus.choices, blank=True, default=""
    )
    last_test_message = models.CharField(max_length=MAX_TEST_MESSAGE_LENGTH, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    objects = IntegrationConnectionQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("category", "name", "id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["client", "category", "status"], name="integ_conn_client_cat_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                "client",
                "category",
                "provider",
                Lower("name"),
                condition=models.Q(archived_at__isnull=True),
                name="integrations_connection_name_unique_active",
            ),
            models.CheckConstraint(
                condition=_in("category", IntegrationCategory),
                name="integrations_connection_category_valid",
            ),
            models.CheckConstraint(
                condition=_in("status", ConnectionStatus),
                name="integrations_connection_status_valid",
            ),
            models.CheckConstraint(
                condition=_in("last_test_status", ConnectionTestStatus, blank=True),
                name="integrations_connection_test_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.category}/{self.provider})"

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials.is_set)

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.config, dict):
            raise ValidationError({"config": "Config must be a JSON object."})


# ------------------------------------------------------------------ usage and cache (#79, #80)


class CallStatus(models.TextChoices):
    OK = "ok", "OK"
    ERROR = "error", "Error"


class ProviderCallQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["ProviderCall"], TenantQuerySet["ProviderCall"]
):
    pass


class ProviderCall(AppendOnlyModel, TenantModel, UUIDModel):
    """One provider call (or one cache hit, or one call refused by a budget): what it cost.

    Append-only and final. It holds NO request or response body and no secret: the optional raw
    payload lives in ``RawPayload`` (policy gated, removable by retention). ``credits`` is the
    provider's own unit, ``cost`` money in ``currency``; both are ``None`` when the provider did
    not report them, and ``0`` for a cache hit. ``error_code`` is a ``ProviderError.code`` (or
    ``budget_exceeded`` when a budget stopped the call before it was made).
    """

    tenant_parent = "connection"

    connection = models.ForeignKey(
        IntegrationConnection, on_delete=models.PROTECT, related_name="calls"
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_calls",
    )
    job = models.ForeignKey(
        "core.Job",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_calls",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    category = models.CharField(max_length=32, choices=IntegrationCategory.choices)
    provider = models.CharField(max_length=64)
    operation = models.CharField(max_length=64)
    status = models.CharField(max_length=8, choices=CallStatus.choices)
    error_code = models.CharField(max_length=64, blank=True, default="")
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    credits = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    cost = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    model = models.CharField(max_length=100, blank=True, default="")
    cache_hit = models.BooleanField(default=False)
    request_id = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    objects = ProviderCallQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["client", "-created_at"], name="integ_call_client_time_idx"),
            models.Index(
                fields=["client", "provider", "-created_at"], name="integ_call_client_prov_idx"
            ),
            models.Index(fields=["connection", "-created_at"], name="integ_call_conn_time_idx"),
            models.Index(fields=["campaign", "-created_at"], name="integ_call_camp_time_idx"),
            models.Index(fields=["job"], name="integ_call_job_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("category", IntegrationCategory),
                name="integrations_call_category_valid",
            ),
            models.CheckConstraint(
                condition=_in("status", CallStatus), name="integrations_call_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(status="ok", error_code="")
                | (models.Q(status="error") & ~models.Q(error_code="")),
                name="integrations_call_error_code_iff_error",
            ),
            models.CheckConstraint(
                condition=models.Q(cache_hit=False) | models.Q(status="ok"),
                name="integrations_call_cache_hit_is_ok",
            ),
            models.CheckConstraint(
                condition=(models.Q(credits__isnull=True) | models.Q(credits__gte=0))
                & (models.Q(cost__isnull=True) | models.Q(cost__gte=0)),
                name="integrations_call_usage_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}.{self.operation} {self.status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.sync_client()  # also done by TenantModel.save; first, so the checks below can trust it
        for name in ("campaign", "job"):
            related = getattr(self, name)
            if related is not None and related.client_id != self.client_id:
                raise TenantMismatchError(
                    f"ProviderCall.{name} belongs to another client than the connection."
                )
        super().save(*args, **kwargs)


class UsageBudgetQuerySet(TenantQuerySet["UsageBudget"]):
    pass


class UsageBudget(TenantModel, BaseModel):
    """Monthly (calendar month, UTC) spending limits of one connection. Optional.

    ``credit_limit`` caps provider credits and ``cost_limit`` caps money in ``currency``; either
    may be empty (no cap on that measure). At ``warn_at_percent`` of a limit calls still run but
    are logged as a warning; at 100% and ``hard_stop`` they are refused with
    ``BudgetExceeded`` (a ``ProviderQuotaExceeded``) before any provider request. Without
    ``hard_stop`` the limit only warns. Change it through ``usage.set_budget`` (audited).
    """

    tenant_parent = "connection"

    connection = models.OneToOneField(
        IntegrationConnection, on_delete=models.PROTECT, related_name="budget"
    )
    credit_limit = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    cost_limit = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    warn_at_percent = models.PositiveSmallIntegerField(default=80)
    hard_stop = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    objects = UsageBudgetQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("created_at", "id")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(credit_limit__isnull=True) | models.Q(credit_limit__gte=0),
                name="integrations_budget_credit_limit_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_limit__isnull=True) | models.Q(cost_limit__gte=0),
                name="integrations_budget_cost_limit_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(warn_at_percent__gte=1, warn_at_percent__lte=100),
                name="integrations_budget_warn_percent_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_limit__isnull=True) | ~models.Q(currency=""),
                name="integrations_budget_cost_needs_currency",
            ),
        ]

    def __str__(self) -> str:
        return f"Budget of {self.connection_id}"


class CachedResponseQuerySet(TenantQuerySet["CachedResponse"]):
    def live(self) -> CachedResponseQuerySet:
        return self.filter(expires_at__gt=timezone.now())


class CachedResponse(TenantModel, UUIDModel):
    """A cached adapter answer for one client's connection and request (issue #80).

    The key is ``sha256(client, connection, provider, operation, canonical request)``, so one
    client never reads another's answers (licences differ per client). ``payload`` is the encoded,
    credential-stripped result, stored only when the adapter's ``CachePolicy`` allows caching;
    it is NOT encrypted at the application level (see ``docs/integrations/usage-and-cache.md``).
    Rows are a cache, not history: a refresh replaces the row, the ``ProviderCall`` log keeps
    every call. ``purge_provider_cache`` deletes expired and no-longer-allowed rows.
    """

    tenant_parent = "connection"

    connection = models.ForeignKey(
        IntegrationConnection, on_delete=models.PROTECT, related_name="cached_responses"
    )
    category = models.CharField(max_length=32, choices=IntegrationCategory.choices)
    provider = models.CharField(max_length=64)
    operation = models.CharField(max_length=64)
    cache_key = models.CharField(max_length=64)
    payload = models.JSONField()
    size_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)

    objects = CachedResponseQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                "connection", "cache_key", name="integrations_cache_connection_key_unique"
            ),
            models.CheckConstraint(
                condition=_in("category", IntegrationCategory),
                name="integrations_cache_category_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}.{self.operation} {self.cache_key[:8]}"


class RawPayloadQuerySet(TenantQuerySet["RawPayload"]):
    pass


class RawPayload(TenantModel, UUIDModel):
    """The raw provider answer of one logged call, kept for audit and re-parsing (issue #80).

    Only written when the adapter's ``CachePolicy.allow_store_raw`` is true; credential-like
    keys and secrets in text are masked first. Removed by retention after ``expires_at``
    (``purge_provider_cache``); the ``ProviderCall`` row stays. Reach it through
    ``call.raw_payload`` or the ``rawpayload:<id>`` reference in ``Provenance.raw_ref``.
    """

    tenant_parent = "call"

    call = models.OneToOneField(ProviderCall, on_delete=models.PROTECT, related_name="raw_payload")
    payload = models.JSONField()
    size_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)

    objects = RawPayloadQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"Raw payload of {self.call_id}"
