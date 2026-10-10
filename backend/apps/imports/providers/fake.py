"""A deterministic in-memory provider for tests and the dev UI (issue #66).

25 placeholder companies (``*.example.com`` domains, some Arabic names, no real company), the
full filter set, cursor paging, sort, ``limit`` and errors on demand:

    provider = FakeCompanyProvider()
    page = provider.search(CompanySearchRequest(countries=["SA"], page_size=5))
    page = provider.search(page_request.with_cursor(page.next_cursor))

Errors on demand: set ``provider.fail_with = ProviderRateLimited(30)`` (raised on every call), or
use a magic query ``!auth``, ``!rate_limited``, ``!unavailable``, ``!bad_request``, ``!quota``.
``provider.calls`` records every request. The clock is fixed so output is reproducible.
Not registered anywhere by default.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NamedTuple

from .base import FilterSupport, ProviderCapabilities
from .errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)
from .types import CompanySearchRequest, CompanySearchResult, ProviderCompany

FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class _Row(NamedTuple):
    slug: str
    name: str
    country: str
    industry: str
    employees: int
    headquarters: str
    business_model: str
    company_type: str


_DATASET: tuple[_Row, ...] = (
    _Row("nile-freight", "Nile Freight Co", "EG", "Logistics", 420, "Cairo", "b2b", "private"),
    _Row("gulf-cargo", "Gulf Cargo Services", "AE", "Logistics", 150, "Dubai", "b2b", "private"),
    _Row(
        "rimal-logistics",
        "رمال للخدمات اللوجستية",
        "SA",
        "Logistics",
        800,
        "Riyadh",
        "b2b",
        "private",
    ),
    _Row("falcon-software", "Falcon Software", "AE", "Software", 60, "Abu Dhabi", "b2b", "startup"),
    _Row("najd-tech", "نجد للتقنية", "SA", "Software", 220, "Riyadh", "b2b", "private"),
    _Row("desert-pay", "Desert Pay", "SA", "Fintech", 95, "Jeddah", "both", "startup"),
    _Row("souq-retail", "Souq Retail Group", "AE", "Retail", 3200, "Dubai", "b2c", "public"),
    _Row(
        "oasis-foods", "واحة للأغذية", "SA", "Food and Beverage", 1100, "Dammam", "both", "private"
    ),
    _Row(
        "atlas-build",
        "Atlas Building Materials",
        "MA",
        "Construction",
        540,
        "Casablanca",
        "b2b",
        "private",
    ),
    _Row(
        "cedar-health", "Cedar Health Systems", "LB", "Healthcare", 75, "Beirut", "b2b", "private"
    ),
    _Row("pearl-hotels", "Pearl Hotels", "QA", "Hospitality", 1800, "Doha", "b2c", "private"),
    _Row(
        "sahara-energy",
        "Sahara Energy Partners",
        "DZ",
        "Energy",
        2500,
        "Algiers",
        "b2b",
        "state_owned",
    ),
    _Row("bayt-realty", "بيت للعقارات", "AE", "Real Estate", 40, "Sharjah", "both", "private"),
    _Row(
        "delta-textiles",
        "Delta Textiles",
        "EG",
        "Manufacturing",
        950,
        "Alexandria",
        "b2b",
        "private",
    ),
    _Row("crescent-bank", "Crescent Digital Bank", "SA", "Fintech", 310, "Riyadh", "b2c", "public"),
    _Row(
        "jordan-pharma",
        "Jordan Valley Pharma",
        "JO",
        "Pharmaceuticals",
        480,
        "Amman",
        "b2b",
        "private",
    ),
    _Row("khaleej-media", "خليج ميديا", "AE", "Media", 55, "Dubai", "b2b", "private"),
    _Row("tahrir-edu", "Tahrir Learning", "EG", "Education", 130, "Cairo", "b2c", "nonprofit"),
    _Row(
        "amber-telecom",
        "Amber Telecom",
        "OM",
        "Telecommunications",
        1500,
        "Muscat",
        "both",
        "public",
    ),
    _Row(
        "harbor-marine",
        "Harbor Marine Services",
        "AE",
        "Logistics",
        260,
        "Jebel Ali",
        "b2b",
        "private",
    ),
    _Row("medina-agri", "Medina Agritech", "SA", "Agriculture", 28, "Medina", "b2b", "startup"),
    _Row("sunrise-solar", "Sunrise Solar", "JO", "Energy", 85, "Amman", "b2b", "startup"),
    _Row(
        "karam-catering", "كرم للضيافة", "KW", "Hospitality", 210, "Kuwait City", "b2c", "private"
    ),
    _Row(
        "zenith-consult",
        "Zenith Consulting",
        "AE",
        "Professional Services",
        18,
        "Dubai",
        "b2b",
        "private",
    ),
    _Row("marina-auto", "Marina Automotive", "BH", "Automotive", 340, "Manama", "both", "private"),
)

MAGIC_ERRORS: dict[str, Callable[[], ProviderError]] = {
    "!auth": ProviderAuthError,
    "!rate_limited": lambda: ProviderRateLimited(30),
    "!unavailable": ProviderUnavailable,
    "!bad_request": ProviderBadRequest,
    "!quota": ProviderQuotaExceeded,
}


def _company(row: _Row) -> ProviderCompany:
    return ProviderCompany(
        name=row.name,
        website=f"https://www.{row.slug}.example.com",
        domain_hint=f"{row.slug}.example.com",
        profile_url=f"https://profiles.example.com/company/{row.slug}",
        country=row.country,
        industry=row.industry,
        employee_count=row.employees,
        headquarters=f"{row.headquarters}, {row.country}",
        description=f"Placeholder {row.industry.lower()} company for tests.",
        provider_id=f"fake-{row.slug}",
        provider_url=f"https://provider.example.com/records/{row.slug}",
        confidence=0.9,
        extra={"business_model": row.business_model, "company_type": row.company_type},
    )


def _matches(row: _Row, req: CompanySearchRequest) -> bool:
    folded = {
        "industries": {i.casefold() for i in req.industries},
        "ex_ind": {i.casefold() for i in req.excluded_industries},
        "ex_type": {t.casefold() for t in req.excluded_company_types},
    }
    if req.query:
        q = req.query.casefold()
        if q not in row.name.casefold() and q not in row.industry.casefold():
            return False
    if req.countries and row.country not in req.countries:
        return False
    if folded["industries"] and row.industry.casefold() not in folded["industries"]:
        return False
    if req.employee_min is not None and row.employees < req.employee_min:
        return False
    if req.employee_max is not None and row.employees > req.employee_max:
        return False
    if (
        req.business_model
        and req.business_model != "both"
        and row.business_model not in (req.business_model, "both")
    ):
        return False
    if row.industry.casefold() in folded["ex_ind"]:
        return False
    return row.company_type.casefold() not in folded["ex_type"]


_SORTS: dict[str, Callable[[_Row], Any]] = {
    "name": lambda r: r.name.casefold(),
    "employee_count_asc": lambda r: (r.employees, r.slug),
    "employee_count_desc": lambda r: (-r.employees, r.slug),
}


class FakeCompanyProvider:
    """Deterministic fake. ``name`` defaults to ``"fake"``; ``terms`` is ``manual``."""

    def __init__(
        self,
        name: str = "fake",
        *,
        fail_with: ProviderError | None = None,
        max_page_size: int = 50,
        clock: Callable[[], datetime] = lambda: FIXED_NOW,
    ) -> None:
        self.name = name
        self.capabilities = ProviderCapabilities(
            terms="manual",
            supports_search=True,
            supports_filters=FilterSupport(
                countries=True, industries=True, size=True, business_model=True
            ),
            max_page_size=max_page_size,
            requires_credentials=False,
        )
        self.fail_with = fail_with
        self.calls: list[CompanySearchRequest] = []
        self._clock = clock

    def search(self, request: CompanySearchRequest) -> CompanySearchResult:
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        magic = MAGIC_ERRORS.get(request.query)
        if magic is not None:
            raise magic()

        offset = 0
        if request.cursor is not None:
            prefix, _, number = request.cursor.partition(":")
            if prefix != "o" or not number.isdecimal() or len(number) > 9:
                raise ProviderBadRequest("cursor", provider_name=self.name)
            offset = int(number)

        rows = [r for r in _DATASET if _matches(r, request)]
        if request.sort in _SORTS:
            rows.sort(key=_SORTS[request.sort])
        # ``limit`` caps the whole import, so pages never reach past it.
        capped = rows[: request.limit]
        size = min(request.page_size, self.capabilities.max_page_size)
        page = capped[offset : offset + size]
        next_offset = offset + len(page)
        return CompanySearchResult(
            items=tuple(_company(r) for r in page),
            provider_name=self.name,
            retrieved_at=self._clock(),
            total_estimate=len(rows),
            next_cursor=f"o:{next_offset}" if page and next_offset < len(capped) else None,
            raw_ref=f"fake-call-{len(self.calls)}",
            credits_used=0.0,
        )


def fake_dataset_size() -> int:
    return len(_DATASET)
