# Nullable text is deliberate: null means "not found" on research facts (never a guessed value),
# and a null domain is what the partial unique index ignores (data-model.md, issue #40).
# ruff: noqa: DJ001
"""Company, CompanyResearch and DataSource (issue #40).

Design: ``docs/data-model.md`` and ADR 0009.

* ``Company`` is the identity of one account inside one campaign (what was entered or
  imported). Only ``status`` and ``archived_at`` change after creation. Create it with
  ``apps.companies.services.create_company``.
* ``CompanyResearch`` is an append-only snapshot of what we knew at ``researched_at``. There
  is no ``is_current`` column: the current snapshot is the latest by ``researched_at``, found
  with ``CompanyResearch.objects.latest_for(company)``. Create with
  ``services.add_research_snapshot``.
* ``DataSource`` is one retrieval of evidence (a provider call, a page fetch, a news article,
  or a person typing a fact). It is append-only and client-scoped. Other apps (signals and
  contacts, issue #41) point at it with a foreign key and must keep ``client_id`` equal to
  the source's. Create with ``services.create_data_source``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
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

from .domain import normalize_domain


class DataSourceType(models.TextChoices):
    PROVIDER = "provider", "Data provider"
    WEBSITE = "website", "Website"
    NEWS = "news", "News"
    MANUAL = "manual", "Manual entry"


class InputSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    CSV = "csv", "CSV import"
    PROVIDER = "provider", "Provider"


class CompanyStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ANALYZING = "analyzing", "Analyzing"
    ANALYZED = "analyzed", "Analyzed"
    FAILED = "failed", "Failed"


class Classification(models.TextChoices):
    B2B = "b2b", "B2B"
    B2C = "b2c", "B2C"
    BOTH = "both", "B2B and B2C"
    UNKNOWN = "unknown", "Unknown"


def _in(field: str, choices: type[models.TextChoices]) -> models.Q:
    return models.Q(**{f"{field}__in": list(choices.values)})


# ------------------------------------------------------------------ DataSource


class DataSourceQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["DataSource"], TenantQuerySet["DataSource"]
):
    pass


class DataSource(AppendOnlyModel, TenantModel, UUIDModel):
    """One retrieval of evidence: where a fact came from and when we got it (Brief section 5).

    Append-only. The same URL fetched twice is two rows, because the page may have changed.
    A person typing a fact is a source of type ``manual`` with themself as ``created_by``.
    A root tenant row: ``client`` is given explicitly (no ``tenant_parent``). Anything that
    references a source (``CompanyResearch``, and later ``Signal`` and ``Contact``) must have
    the same ``client_id`` as the source.

    Dates: ``retrieved_at`` is when we fetched or typed it. ``evidence_date`` is optional and
    is the date the source itself carries (publication or event date), when it states one.
    """

    type = models.CharField(max_length=16, choices=DataSourceType.choices)
    name = models.CharField(
        max_length=200, help_text="Provider name, site or publication, or who typed it."
    )
    url = models.URLField(
        max_length=2000, blank=True, help_text="Required unless type is manual or provider."
    )
    provider_reference = models.CharField(
        max_length=200, blank=True, help_text="The provider's own record id (ADR 0002)."
    )
    retrieved_at = models.DateTimeField(default=timezone.now)
    evidence_date = models.DateField(
        null=True, blank=True, help_text="Date stated by the source itself, if any."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DataSourceQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-retrieved_at", "-created_at")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["client", "-retrieved_at"], name="co_ds_client_retrieved_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("type", DataSourceType), name="companies_datasource_type_valid"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(type__in=[DataSourceType.MANUAL, DataSourceType.PROVIDER])
                    | ~models.Q(url="")
                ),
                name="companies_datasource_url_required",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type}: {self.name}"


# ------------------------------------------------------------------ Company


class CompanyQuerySet(ArchivableQuerySet["Company"], TenantQuerySet["Company"]):  # type: ignore[override]
    def named(self, name: str) -> CompanyQuerySet:
        """Case-insensitive exact name match (the same-name warning when there is no domain)."""
        return self.annotate(_lowered_name=Lower("name")).filter(_lowered_name=name.strip().lower())


class Company(ArchivableModel, TenantModel, BaseModel):
    """The identity of one account inside one campaign.

    ``client`` is copied from the campaign. ``domain`` is derived from ``website`` by
    ``normalize_domain`` on every save and is unique per campaign when set (archived rows
    count: a duplicate import restores the archived row instead of inserting). Researched facts
    live in ``CompanyResearch``; correcting a fact is a new snapshot, not an edit here.
    """

    tenant_parent = "campaign"

    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, related_name="companies"
    )
    name = models.CharField(max_length=300)
    website = models.CharField(max_length=2000, blank=True, help_text="As entered.")
    domain = models.CharField(
        max_length=253, null=True, blank=True, editable=False, help_text="Normalized, or null."
    )
    profile_url = models.URLField(max_length=2000, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2, upper.")
    input_source = models.CharField(
        max_length=16, choices=InputSource.choices, default=InputSource.MANUAL
    )
    status = models.CharField(
        max_length=16, choices=CompanyStatus.choices, default=CompanyStatus.PENDING
    )
    # Becomes a FK to core.Job once issue #44 lands (the import job that created the row).
    created_by_job_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("name", "created_at")
        verbose_name_plural = "companies"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["client", "domain"], name="co_company_client_domain_idx"),
            models.Index(fields=["campaign", "status"], name="co_company_camp_status_idx"),
            models.Index(fields=["campaign", "archived_at"], name="co_company_camp_arch_idx"),
            models.Index(Lower("name"), "campaign", name="co_company_camp_lname_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["campaign", "domain"],
                condition=models.Q(domain__isnull=False),
                name="companies_company_domain_unique_per_campaign",
            ),
            models.CheckConstraint(
                condition=_in("status", CompanyStatus), name="companies_company_status_valid"
            ),
            models.CheckConstraint(
                condition=_in("input_source", InputSource),
                name="companies_company_input_source_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="companies_company_name_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.website.strip() and normalize_domain(self.website) is None:
            raise ValidationError({"website": "Enter a website with a valid domain name."})
        if self.country and not (
            len(self.country) == 2 and self.country.isascii() and self.country.isupper()
        ):
            raise ValidationError({"country": "Use an ISO 3166-1 alpha-2 code in upper case."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.domain = normalize_domain(self.website)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "website" in update_fields:
            kwargs["update_fields"] = {*update_fields, "domain"}
        super().save(*args, **kwargs)

    @property
    def latest_research(self) -> CompanyResearch | None:
        """The current research snapshot (latest ``researched_at``), or ``None``."""
        return CompanyResearch.objects.latest_for(self)


# ------------------------------------------------------------------ CompanyResearch


class CompanyResearchQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["CompanyResearch"], TenantQuerySet["CompanyResearch"]
):
    def latest_first(self) -> CompanyResearchQuerySet:
        """Newest snapshot first: ``researched_at`` desc, ties by ``created_at`` then ``id``."""
        return self.order_by("-researched_at", "-created_at", "-id")

    def latest_for(self, company: Company | Any) -> CompanyResearch | None:
        """The current snapshot of one company (a ``Company`` or its id), or ``None``.

        This is the single definition of "current" (no ``is_current`` column, ADR 0009).
        """
        return self.filter(company=company).latest_first().first()

    def current(self) -> CompanyResearchQuerySet:
        """Only each company's latest snapshot, for list and dashboard queries."""
        outer = models.OuterRef
        newer = CompanyResearch.objects.filter(company=outer("company")).filter(
            models.Q(researched_at__gt=outer("researched_at"))
            | models.Q(
                researched_at=outer("researched_at"),
                created_at__gt=outer("created_at"),
            )
            | models.Q(
                researched_at=outer("researched_at"),
                created_at=outer("created_at"),
                id__gt=outer("id"),
            )
        )
        return self.filter(~models.Exists(newer))


class CompanyResearch(AppendOnlyModel, TenantModel, UUIDModel):
    """One immutable snapshot of what we knew about a company at ``researched_at`` (Brief 5).

    Every fact column is nullable (providers return gaps); null means "not found", never a
    guess. ``researched_at`` is when the information was retrieved and may be earlier than
    ``created_at`` for imports. An older snapshot imported later does not become current.
    Percent changes are numbers such as ``-12.00`` for -12%.
    """

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="research")
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="company_research"
    )
    researched_at = models.DateTimeField(default=timezone.now)

    industry = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    headquarters = models.CharField(max_length=300, null=True, blank=True)
    employee_count = models.PositiveIntegerField(null=True, blank=True)
    business_model = models.TextField(null=True, blank=True, help_text="Free text, as found.")
    classification = models.CharField(
        max_length=8, choices=Classification.choices, null=True, blank=True
    )
    products_services = models.TextField(null=True, blank=True)
    target_customers = models.TextField(null=True, blank=True)

    sales_headcount = models.PositiveIntegerField(null=True, blank=True)
    bd_headcount = models.PositiveIntegerField(null=True, blank=True)
    marketing_headcount = models.PositiveIntegerField(null=True, blank=True)
    commercial_partnerships_headcount = models.PositiveIntegerField(null=True, blank=True)
    department_growth = models.JSONField(
        null=True, blank=True, help_text="Free-form, where the provider gives it."
    )
    headcount_change_3m = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, help_text="Percent, -12% is -12.00."
    )
    headcount_change_6m = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    headcount_change_12m = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CompanyResearchQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-researched_at", "-created_at", "-id")
        verbose_name_plural = "company research"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["company", "-researched_at", "-created_at", "-id"],
                name="co_research_latest_idx",
            ),
            models.Index(fields=["client"], name="co_research_client_idx"),
            models.Index(fields=["data_source"], name="co_research_source_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(classification__isnull=True)
                | models.Q(classification__in=list(Classification.values)),
                name="companies_research_classification_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.company_id} @ {self.researched_at:%Y-%m-%d}"

    def sync_client(self) -> None:
        super().sync_client()
        if self.data_source.client_id != self.client_id:
            raise TenantMismatchError(
                "CompanyResearch.data_source belongs to a different client than its company."
            )
