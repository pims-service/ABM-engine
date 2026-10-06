"""Write path for companies, research snapshots and data sources.

Permission checks belong to the API layer (issue #46); these functions enforce data rules.

* ``create_company``: dedupe by normalized domain per campaign, restore an archived duplicate,
  warn (never block) on a same-name company when there is no domain.
* ``add_research_snapshot``: append a new ``CompanyResearch`` row. Never edits an old one.
* ``create_data_source``: record one retrieval of evidence for a client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.campaigns.models import Campaign, Client

from .domain import normalize_domain
from .models import (
    Company,
    CompanyResearch,
    DataSource,
    DataSourceType,
    InputSource,
)

__all__ = [
    "CompanyResult",
    "add_research_snapshot",
    "create_company",
    "create_data_source",
    "normalize_domain",
]

RESEARCH_FIELDS = (
    "industry",
    "description",
    "headquarters",
    "employee_count",
    "business_model",
    "classification",
    "products_services",
    "target_customers",
    "sales_headcount",
    "bd_headcount",
    "marketing_headcount",
    "commercial_partnerships_headcount",
    "department_growth",
    "headcount_change_3m",
    "headcount_change_6m",
    "headcount_change_12m",
)


@dataclass
class CompanyResult:
    """Outcome of ``create_company``.

    ``created`` is True for a new row. A duplicate (same normalized domain in the campaign)
    returns the existing company with ``duplicate`` True and ``restored`` True when it had been
    archived. ``similar`` lists active same-name companies when the new one has no domain
    (a warning for a person to decide on; nothing is merged automatically).
    """

    company: Company
    created: bool = False
    duplicate: bool = False
    restored: bool = False
    similar: list[Company] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        if self.similar:
            return [f"A company named '{self.company.name}' already exists in this campaign."]
        return []


def create_data_source(
    client: Client,
    type: str,
    name: str,
    *,
    url: str = "",
    provider_reference: str = "",
    retrieved_at: datetime | None = None,
    evidence_date: Any = None,
    user: User | None = None,
) -> DataSource:
    """Insert one ``DataSource`` (append-only). ``retrieved_at`` defaults to now."""
    extra: dict[str, Any] = {} if retrieved_at is None else {"retrieved_at": retrieved_at}
    source = DataSource(
        client=client,
        type=type,
        name=name.strip(),
        url=url.strip(),
        provider_reference=provider_reference.strip(),
        evidence_date=evidence_date,
        created_by=user,
        **extra,
    )
    source.full_clean(exclude=["client"], validate_constraints=False)
    if type not in (DataSourceType.MANUAL, DataSourceType.PROVIDER) and not source.url:
        raise ValidationError({"url": "A URL is required unless the source is manual or provider."})
    source.save()
    return source


def _find_by_domain(campaign: Campaign, domain: str) -> Company | None:
    return Company.objects.filter(campaign=campaign, domain=domain).first()


def _duplicate_result(company: Company) -> CompanyResult:
    restored = company.is_archived
    if restored:
        company.restore()
    return CompanyResult(company=company, duplicate=True, restored=restored)


def create_company(
    campaign: Campaign,
    name: str,
    website: str = "",
    *,
    profile_url: str = "",
    country: str = "",
    input_source: str = InputSource.MANUAL,
    user: User | None = None,
    created_by_job_id: Any = None,
) -> CompanyResult:
    """Add a company to a campaign, or report the one that is already there.

    * Same normalized domain already in the campaign (archived or not): nothing is inserted
      (the database also forbids it). The existing company is returned with ``duplicate=True``
      and, if it was archived, restored (``restored=True``).
    * No domain: the company is created, and ``similar`` lists active companies of the same
      name (case-insensitive) as a warning.
    * An archived campaign takes no new companies. A website that has no valid domain name is
      a ``ValidationError``.
    """
    name = name.strip()
    website = website.strip()
    if not name:
        raise ValidationError({"name": "A company needs a name."})
    if campaign.is_archived:
        raise ValidationError("Cannot add a company to an archived campaign.")
    domain = normalize_domain(website)
    if website and domain is None:
        raise ValidationError({"website": "Enter a website with a valid domain name."})

    if domain is not None:
        existing = _find_by_domain(campaign, domain)
        if existing is not None:
            return _duplicate_result(existing)

    company = Company(
        campaign=campaign,
        name=name,
        website=website,
        profile_url=profile_url.strip(),
        country=country.strip().upper(),
        input_source=input_source,
        created_by=user,
        created_by_job_id=created_by_job_id,
    )
    company.full_clean(
        exclude=["client", "campaign"], validate_unique=False, validate_constraints=False
    )
    try:
        with transaction.atomic():  # savepoint: a race with another writer must not break ours
            company.save()
    except IntegrityError:
        if domain is None:
            raise
        existing = _find_by_domain(campaign, domain)
        if existing is None:
            raise
        return _duplicate_result(existing)

    similar: list[Company] = []
    if domain is None:
        similar = list(
            Company.objects.filter(campaign=campaign).active().named(name).exclude(pk=company.pk)
        )
    return CompanyResult(company=company, created=True, similar=similar)


@transaction.atomic
def add_research_snapshot(
    company: Company,
    data_source: DataSource,
    *,
    researched_at: datetime | None = None,
    **facts: Any,
) -> CompanyResearch:
    """Append a research snapshot (history, Brief section 19). Old snapshots are untouched.

    ``facts`` are any of ``RESEARCH_FIELDS``; omitted facts stay null ("not found"). The data
    source must belong to the same client as the company. ``researched_at`` is when the data
    was retrieved (defaults to the source's ``retrieved_at``) and decides which snapshot is
    current, so back-filling an older snapshot does not replace a newer one.
    """
    unknown = sorted(set(facts) - set(RESEARCH_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown field."))
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")
    if data_source.client_id != company.client_id:
        raise ValidationError({"data_source": "The data source belongs to a different client."})

    snapshot = CompanyResearch(
        company=company,
        data_source=data_source,
        researched_at=researched_at or data_source.retrieved_at,
        **facts,
    )
    snapshot.full_clean(
        exclude=["client", "company", "data_source"],
        validate_unique=False,
        validate_constraints=False,
    )
    snapshot.save()
    return snapshot
