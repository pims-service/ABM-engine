"""Signal: one trigger-type event for a company (issue #41, Brief section 7).

Design: ``docs/data-model.md`` ("Signal", "Trigger freshness") and ADR 0008 / 0009.

* A company can have many signals, or none. A company with zero signals is simply "no
  trigger" and that never affects ICP fit or eligibility.
* Signals are append-only. There is **no** ``active`` flag and **no** stored Yes/No. Trigger is
  computed: Yes when at least one signal is *fresh* (``expires_at`` null or in the future) and
  *current* (nothing supersedes it). Use ``Signal.objects.fresh(as_of)`` /
  ``.current()`` or ``services.company_trigger_state(company, as_of)``.
* ``expires_at`` null means "no freshness rule applied yet" and counts as fresh until the
  per-type rules land (M5). Rules are never applied by rewriting old rows.
* A wrong signal is corrected by a new row with ``supersedes`` pointing at it, never edited.
* ``data_source`` and ``event_date`` are NOT NULL in the database: no source, no signal. An AI
  model's inference is never a source (there is no AI ``DataSourceType``); AI provenance is
  only recorded in ``model_name`` / ``prompt_version`` / ``schema_version``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.companies.models import Company, DataSource
from apps.core.base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)


class SignalType(models.TextChoices):
    """The thirteen trigger kinds of Brief section 7."""

    SALES_HIRING = "sales_hiring", "Sales hiring"
    BD_HIRING = "bd_hiring", "Business development hiring"
    COMMERCIAL_HIRING = "commercial_hiring", "Commercial hiring"
    PARTNERSHIPS_HIRING = "partnerships_hiring", "Partnerships hiring"
    HEADCOUNT_GROWTH = "headcount_growth", "Headcount growth"
    COMMERCIAL_TEAM_GROWTH = "commercial_team_growth", "Commercial team growth"
    FUNDING = "funding", "Funding"
    MARKET_EXPANSION = "market_expansion", "Market expansion"
    NEW_LEADERSHIP = "new_leadership", "New leadership"
    NEW_OFFICE = "new_office", "New office"
    NEW_PRODUCT = "new_product", "New product"
    MAJOR_PARTNERSHIP = "major_partnership", "Major partnership"
    OTHER = "other", "Other"


class SignalQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["Signal"], TenantQuerySet["Signal"]
):
    def latest_first(self) -> SignalQuerySet:
        return self.order_by("-event_date", "-detected_at", "-id")

    def current(self) -> SignalQuerySet:
        """Signals nothing supersedes (corrected rows drop out)."""
        return self.filter(superseded_by__isnull=True)

    def fresh(self, as_of: datetime | None = None) -> SignalQuerySet:
        """Current signals that have not expired at ``as_of`` (default: now).

        ``expires_at`` null means no freshness rule yet and counts as fresh. A signal is stale
        at the exact instant it expires (``expires_at > as_of`` is fresh). Supersession is
        evaluated as of now, not as of ``as_of``.
        """
        if as_of is None:
            as_of = timezone.now()
        elif timezone.is_naive(as_of):
            raise ValueError("as_of must be timezone-aware.")
        return self.current().filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=as_of)
        )

    def for_company(self, company: Company | Any) -> SignalQuerySet:
        return self.filter(company=company)


class Signal(AppendOnlyModel, TenantModel, UUIDModel):
    """One immutable piece of timing evidence for a company (Brief section 7)."""

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="signals")
    data_source = models.ForeignKey(DataSource, on_delete=models.PROTECT, related_name="signals")
    type = models.CharField(max_length=32, choices=SignalType.choices)
    evidence = models.TextField(help_text="Quoted or summarized from the source.")
    event_date = models.DateField(help_text="When the event happened, as the source states it.")
    detected_at = models.DateTimeField(default=timezone.now, help_text="When we found it.")
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Null: no freshness rule applied yet (counts as fresh)."
    )
    supersedes = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
        help_text="The earlier signal this row corrects or replaces.",
    )
    model_name = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    schema_version = models.CharField(max_length=50, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SignalQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-event_date", "-detected_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["company", "expires_at"], name="rs_signal_company_expiry_idx"),
            models.Index(fields=["client"], name="rs_signal_client_idx"),
            models.Index(fields=["data_source"], name="rs_signal_source_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(type__in=list(SignalType.values)),
                name="research_signal_type_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(evidence=""), name="research_signal_evidence_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type} @ {self.event_date}"

    @property
    def is_current(self) -> bool:
        """True when no later signal supersedes this one."""
        return not Signal.objects.filter(supersedes=self).exists()

    def is_fresh(self, as_of: datetime | None = None) -> bool:
        return Signal.objects.fresh(as_of).filter(pk=self.pk).exists()

    def sync_client(self) -> None:
        super().sync_client()
        if self.data_source.client_id != self.client_id:
            raise TenantMismatchError(
                "Signal.data_source belongs to a different client than its company."
            )
        if self.supersedes is not None and self.supersedes.company_id != self.company_id:
            raise TenantMismatchError("Signal.supersedes belongs to a different company.")
