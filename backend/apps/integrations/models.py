"""Integration settings: one row per provider connection of a client (issue #76, Brief section 17).

A client can connect any number of providers per category (an LLM, a company-data provider, a
CRM, ...). A row holds the non-secret ``config`` in plain JSON and the secrets in
``credentials``, which is encrypted at rest (``apps.integrations.crypto``) and write-only through
the API. Adapters (issues #74, #82 to #85) read credentials only through
``apps.integrations.services.get_credentials``.

Change connections through ``apps.integrations.services`` so every change is audited, without
secret values. Rows are archived, never deleted.
"""

from __future__ import annotations

import re
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower

from apps.core.base import (
    ArchivableModel,
    ArchivableQuerySet,
    BaseModel,
    TenantModel,
    TenantQuerySet,
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
