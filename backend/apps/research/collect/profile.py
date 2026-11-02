"""The company profile collector (issue #93, Brief sections 5 and 6).

Asks the company-data adapter about one company and appends a research snapshot with what it
returned: industry, description, headquarters, headcount, the sales / BD / marketing / commercial
and partnerships headcounts, and the growth over 3, 6 and 12 months (worked out by the growth
module, with the provider's own percentages as a fallback). Fields the provider did not return
stay empty and are listed as ``missing_fields``; nothing is guessed.

Running it again with unchanged provider data adds neither a snapshot nor a source (idempotent);
changed data adds a new snapshot and the old one stays in the history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from apps.accounts.models import User
from apps.companies.models import Company, CompanyResearch, DataSource, DataSourceType
from apps.companies.services import add_research_snapshot
from apps.integrations.framework import CompanyRef, NormalizedCompany, Provenance
from apps.research.evidence import record_evidence
from apps.research.growth import (
    GrowthReport,
    HeadcountPoint,
    ProviderGrowth,
    growth_from_points,
    to_research_fields,
)

#: The snapshot fields a provider can fill, and so what ``missing_fields`` is measured against.
PROVIDER_FIELDS = (
    "industry",
    "description",
    "headquarters",
    "employee_count",
    "sales_headcount",
    "bd_headcount",
    "marketing_headcount",
    "commercial_partnerships_headcount",
    "headcount_change_3m",
    "headcount_change_6m",
    "headcount_change_12m",
)

CREATED = "created"
UNCHANGED = "unchanged"
EMPTY = "empty"


@dataclass
class ProfileOutcome:
    """What the profile step did. ``status`` is ``created``, ``unchanged`` or ``empty``."""

    status: str
    snapshot: CompanyResearch | None = None
    source: DataSource | None = None
    missing_fields: list[str] = field(default_factory=list)
    company_data: NormalizedCompany | None = None
    provenance: Provenance | None = None
    growth: GrowthReport | None = None


def company_ref(company: Company) -> CompanyRef:
    return CompanyRef(
        name=company.name,
        domain=company.domain or None,
        website=company.website or None,
        profile_url=company.profile_url or None,
    )


def _sum(*counts: int | None) -> int | None:
    known = [c for c in counts if c is not None]
    return sum(known) if known else None


#: The snapshot columns that hold one department's headcount. The combined commercial and
#: partnerships column is left out: it is a sum, not a department of its own.
_HISTORY_DEPARTMENTS = (
    ("sales_headcount", "sales"),
    ("bd_headcount", "bd"),
    ("marketing_headcount", "marketing"),
)


def _history(company: Company, provider: str, as_of: date) -> list[HeadcountPoint]:
    """Earlier headcounts this provider gave for ``company``, as observations on the days they
    were retrieved. They are what lets growth be measured from our own history when a provider
    only ever states today's headcount. A snapshot from the same day is the answer being
    processed now, so it is left out."""
    points: list[HeadcountPoint] = []
    snapshots = CompanyResearch.objects.filter(
        company=company,
        data_source__type=DataSourceType.PROVIDER,
        data_source__name=provider,
    ).latest_first()
    for snapshot in snapshots:
        day = snapshot.researched_at.date()
        if day == as_of:
            continue
        reference = f"snapshot:{snapshot.pk}"
        if snapshot.employee_count is not None:
            points.append(HeadcountPoint(day, snapshot.employee_count, reference))
        for column, department in _HISTORY_DEPARTMENTS:
            count = getattr(snapshot, column)
            if count is not None:
                points.append(HeadcountPoint(day, count, reference, department))
    return points


def _growth(
    data: NormalizedCompany, provenance: Provenance, history: list[HeadcountPoint]
) -> GrowthReport:
    """The growth report of one provider answer: one observation per headcount it states, the
    earlier observations from our own history, and the percentages the provider gives directly
    for the windows the observations cannot answer."""
    as_of = provenance.retrieved_at.date()
    reference = f"provider:{provenance.provider}:{provenance.source_ref or 'unspecified'}"
    points: list[HeadcountPoint] = []
    if data.employee_count is not None:
        points.append(HeadcountPoint(as_of, data.employee_count, reference))
    for item in data.departments:
        if item.count is not None:
            points.append(HeadcountPoint(as_of, item.count, reference, item.department))
    points.extend(history)
    provided: dict[str | None, ProviderGrowth] = {}

    def offer(key: str | None, a: Decimal | None, b: Decimal | None, c: Decimal | None) -> None:
        if any(v is not None for v in (a, b, c)):
            provided[key] = ProviderGrowth(reference, {3: a, 6: b, 12: c})

    offer(None, data.headcount_change_3m, data.headcount_change_6m, data.headcount_change_12m)
    for item in data.departments:
        offer(item.department, item.change_3m, item.change_6m, item.change_12m)
    return growth_from_points(points, as_of, provider=provided)


def facts_of(data: NormalizedCompany, growth: GrowthReport) -> dict[str, Any]:
    """The snapshot columns for a provider answer; unknown stays ``None``."""

    def count(name: str) -> int | None:
        item = data.department(name)
        return item.count if item else None

    fields = to_research_fields(growth)
    return {
        "industry": data.industry,
        "description": data.description,
        "headquarters": data.headquarters,
        "employee_count": data.employee_count,
        "sales_headcount": count("sales"),
        "bd_headcount": count("bd"),
        "marketing_headcount": count("marketing"),
        "commercial_partnerships_headcount": _sum(count("commercial"), count("partnerships")),
        "headcount_change_3m": fields["headcount_change_3m"],
        "headcount_change_6m": fields["headcount_change_6m"],
        "headcount_change_12m": fields["headcount_change_12m"],
        "department_growth": fields["department_growth"],
    }


def _same(previous: CompanyResearch, facts: dict[str, Any]) -> bool:
    return all(
        str(getattr(previous, name) or "") == str(facts.get(name) or "") for name in PROVIDER_FIELDS
    )


def collect_profile(company: Company, adapter: Any, *, user: User | None = None) -> ProfileOutcome:
    """Collect the provider profile of ``company`` (see the module docstring)."""
    result = adapter.enrich_company(company_ref(company))
    data: NormalizedCompany | None = result.data
    if data is None:
        return ProfileOutcome(EMPTY, missing_fields=list(PROVIDER_FIELDS))
    provenance: Provenance = result.provenance
    growth = _growth(
        data, provenance, _history(company, provenance.provider, provenance.retrieved_at.date())
    )
    facts = facts_of(data, growth)
    missing = [name for name in PROVIDER_FIELDS if facts.get(name) is None]
    outcome = ProfileOutcome(
        CREATED,
        missing_fields=missing,
        company_data=data,
        provenance=provenance,
        growth=growth,
    )

    previous = (
        CompanyResearch.objects.filter(
            company=company,
            data_source__type=DataSourceType.PROVIDER,
            data_source__name=provenance.provider,
        )
        .latest_first()
        .select_related("data_source")
        .first()
    )
    if previous is not None and _same(previous, facts):
        outcome.status = UNCHANGED
        outcome.snapshot = previous
        outcome.source = previous.data_source
        return outcome

    outcome.source = record_evidence(
        company.client,
        DataSourceType.PROVIDER,
        provenance.provider,
        url=provenance.source_url or "",
        provider_reference=provenance.source_ref or provenance.request_id or "",
        observed_at=provenance.retrieved_at,
        evidence_date=provenance.evidence_date,
        user=user,
    )
    found = {name: value for name, value in facts.items() if value is not None}
    outcome.snapshot = add_research_snapshot(
        company, outcome.source, researched_at=provenance.retrieved_at, **found
    )
    return outcome
