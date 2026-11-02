"""Apollo company-data adapter: search, enrichment, headcount by department, growth, funding and
open job postings from Apollo's official API.

Checked against Apollo's own API reference (https://docs.apollo.io) on 2026-11-01:

* Base ``https://api.apollo.io/api/v1``. The key goes in the ``x-api-key`` header.
* Organization Enrichment: ``GET /organizations/enrich`` with ``domain``, ``website``,
  ``linkedin_url`` (one is needed) and optionally ``name``. A hit answers ``{"organization":
  {...}}`` with ``id``, ``name``, ``website_url``, ``primary_domain``, ``industry``,
  ``short_description``, ``estimated_num_employees``, ``departmental_head_count`` (an object of
  counts, for example ``sales`` and ``business_development``), the growth fields
  ``organization_headcount_six_month_growth`` and ``..._twelve_month_growth`` (the docs' example
  values, 12.3 and 24.6, are percentages), ``total_funding``, ``latest_funding_stage``,
  ``latest_funding_round_date`` and ``funding_events``. One credit per organization.
* Organization Search: ``POST /mixed_companies/search`` with query parameters
  ``organization_num_employees_ranges[]``, ``organization_locations[]``,
  ``q_organization_keyword_tags[]``, ``q_organization_name``, ``page`` and ``per_page`` (at most
  100). Answers ``{"organizations": [...], "pagination": {"page", "per_page", "total_entries",
  "total_pages"}}``. One credit per page; at most 500 pages. Paid plans only (``403`` otherwise).
* Organization Job Postings: ``GET /organizations/{id}/job_postings`` answers
  ``{"organization_job_postings": [{"id", "title", "url", "posted_at", "last_seen_at", "city",
  "state", "country"}]}``. One credit per page.
* Errors: ``401`` (bad key), ``403`` (the key may not use the endpoint), ``422`` (a bad
  parameter), ``429`` (about 600 calls per hour, depending on the plan).

NOT verified against a live key (none was available): what Apollo answers when it knows no such
company (an empty ``organization``, a ``404`` and a ``422`` are all handled as "not found" or a
bad request), the exact credit cost of a failed lookup (taken as 0), and whether a search filter
by industry keyword matches the way an industry name would (industries are sent as keyword
tags). Apollo has no documented free health endpoint, so the connection test makes one
enrichment call (one credit); ``test_connection_costs_credits`` says so.

Apollo sends no ISO country codes, so the country comes from the shared country name table.
There is no three-month growth figure and no commercial or partnerships headcount: those stay
unknown. Signals other than job postings are not offered.

The ``Transport`` is injectable; tests never use the network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar
from urllib.parse import quote, urlencode, urlsplit

from apps.imports.providers.types import CompanySearchRequest
from apps.integrations.adapters.anthropic.transport import (
    HttpRequest,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from apps.integrations.adapters.countries import to_iso, to_name
from apps.integrations.framework import (
    AdapterCapabilities,
    AdapterResult,
    CompanyDataAdapter,
    CompanyPage,
    CompanyRef,
    ConfigIssue,
    ConfigValidation,
    ConnectionTestResult,
    NormalizedCompany,
    ProviderBadRequest,
    ProviderInvalidOutput,
    ProviderUpstreamError,
    SecretStr,
    Usage,
    map_http_status,
)
from apps.integrations.framework.categories import (
    DEPARTMENT_HEADCOUNT,
    ENRICH_COMPANY,
    FUNDING,
    HEADCOUNT_GROWTH_6M,
    HEADCOUNT_GROWTH_12M,
    JOB_POSTINGS,
    SEARCH,
)
from apps.integrations.framework.normalized import (
    DepartmentHeadcount,
    Funding,
    NormalizedJobPosting,
)

API_HOST = "api.apollo.io"
DEFAULT_API_BASE = f"https://{API_HOST}/api/v1"
MAX_PAGE_SIZE = 100
#: The open end of an employee range: Apollo wants two numbers.
_OPEN_END = 1_000_000

#: Apollo's department names, in the app's department vocabulary. The rest are not tracked.
_DEPARTMENTS = {"sales": "sales", "marketing": "marketing", "business_development": "bd"}
_AMOUNT = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmMbB])?\s*$")
_MULTIPLIER = {"k": Decimal(1_000), "m": Decimal(1_000_000), "b": Decimal(1_000_000_000)}


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object) -> Decimal | None:
    """A finite ``Decimal`` from a JSON number; booleans and text are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _day(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _amount(value: object) -> Decimal | None:
    """``"100M"`` -> 100000000. Anything else is unknown."""
    text = _text(value)
    match = _AMOUNT.match(text) if text else None
    if match is None:
        return None
    number = Decimal(match.group(1))
    suffix = match.group(2)
    return number * _MULTIPLIER[suffix.lower()] if suffix else number


class ApolloAdapter(CompanyDataAdapter):
    """Company search and enrichment from Apollo (a paid plan is needed for search)."""

    key = "apollo"
    display_name = "Apollo"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset(
            {
                SEARCH,
                ENRICH_COMPANY,
                JOB_POSTINGS,
                DEPARTMENT_HEADCOUNT,
                HEADCOUNT_GROWTH_6M,
                HEADCOUNT_GROWTH_12M,
                FUNDING,
            }
        ),
        requires_credentials=True,
        max_page_size=MAX_PAGE_SIZE,
        allowed_hosts=(API_HOST,),
        test_connection_costs_credits=True,
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "api_base": {
                "type": "string",
                "default": DEFAULT_API_BASE,
                "maxLength": 200,
                "description": "HTTPS base URL; the host must be on the allow list.",
            },
            "timeout_seconds": {
                "type": "number",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
                "description": "Connect and read timeout per request.",
            },
        },
        "additionalProperties": False,
        "x-credentials": {"required": ["api_key"], "optional": []},
    }

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(config, credentials, clock=clock)
        self._transport: Transport = transport or UrllibTransport(
            self.capabilities.allowed_hosts, provider_name=self.key
        )

    @classmethod
    def validate_config(
        cls, config: Mapping[str, Any], credentials: Mapping[str, SecretStr | str] | None = None
    ) -> ConfigValidation:
        base = super().validate_config(config, credentials)
        issues = list(base.issues)
        api_base = config.get("api_base")
        if isinstance(api_base, str) and not any(i.field == "api_base" for i in issues):
            parts = urlsplit(api_base)
            try:
                port = parts.port
            except ValueError:
                port = -1
            if (
                parts.scheme != "https"
                or (parts.hostname or "").lower() not in cls.capabilities.allowed_hosts
                or parts.username is not None
                or port not in (None, 443)
                or parts.query
                or parts.fragment
            ):
                issues.append(ConfigIssue("api_base", "enum"))
        return ConfigValidation(tuple(issues))

    # ------------------------------------------------------------------ requests
    def _send(self, method: str, path: str, query: Sequence[tuple[str, str]] = ()) -> HttpResponse:
        url = str(self.config["api_base"]).rstrip("/") + path
        if query:
            url += "?" + urlencode(list(query))
        headers = {
            "x-api-key": self.credential("api_key").reveal(),
            "accept": "application/json",
            "content-type": "application/json",
        }
        request = HttpRequest(
            method, url, headers, None, timeout=float(self.config["timeout_seconds"])
        )
        return self._transport.send(request)

    def _json(self, response: HttpResponse) -> dict[str, Any]:
        try:
            decoded = json.loads(response.body)
        except ValueError:
            raise ProviderUpstreamError(provider_name=self.key) from None
        if not isinstance(decoded, dict):
            raise ProviderUpstreamError(provider_name=self.key)
        return decoded

    def _raise_for(self, response: HttpResponse) -> None:
        raise map_http_status(
            response.status,
            provider_name=self.key,
            retry_after=response.headers.get("retry-after"),
        )

    # ------------------------------------------------------------------ mapping
    def _company(self, org: Mapping[str, Any]) -> NormalizedCompany:
        departments = []
        counts = org.get("departmental_head_count")
        if isinstance(counts, Mapping):
            for apollo_name, department in _DEPARTMENTS.items():
                count = _count(counts.get(apollo_name))
                if count is not None:
                    departments.append(DepartmentHeadcount(department, count))
        place = ", ".join(
            part
            for part in (_text(org.get("city")), _text(org.get("state")), _text(org.get("country")))
            if part
        )
        try:
            return NormalizedCompany(
                name=_text(org.get("name")) or "",
                provider_id=_text(org.get("id")),
                website=_text(org.get("website_url")),
                domain=_text(org.get("primary_domain")),
                profile_url=_text(org.get("linkedin_url")),
                country=to_iso(org.get("country")),
                industry=_text(org.get("industry")),
                description=_text(org.get("short_description")),
                headquarters=place or None,
                employee_count=_count(org.get("estimated_num_employees")),
                departments=tuple(departments),
                headcount_change_6m=_number(org.get("organization_headcount_six_month_growth")),
                headcount_change_12m=_number(org.get("organization_headcount_twelve_month_growth")),
                funding=self._funding(org),
            )
        except ValueError:
            raise ProviderInvalidOutput(provider_name=self.key) from None

    @staticmethod
    def _funding(org: Mapping[str, Any]) -> Funding | None:
        last_amount: Decimal | None = None
        currency: str | None = None
        events = org.get("funding_events")
        if isinstance(events, list):
            dated = [e for e in events if isinstance(e, Mapping) and _day(e.get("date"))]
            if dated:
                newest = max(dated, key=lambda e: _day(e.get("date")) or date.min)
                last_amount = _amount(newest.get("amount"))
                if newest.get("currency") == "$":
                    currency = "USD"
        total = _number(org.get("total_funding"))
        stage = _text(org.get("latest_funding_stage"))
        when = _day(org.get("latest_funding_round_date"))
        if total is None and stage is None and when is None:
            return None
        return Funding(
            total_raised=total,
            last_round_type=stage,
            last_round_amount=last_amount,
            last_round_date=when,
            currency=currency if last_amount is not None or total is not None else None,
        )

    # ------------------------------------------------------------------ operations
    def test_connection(self) -> ConnectionTestResult:
        """One enrichment call for Apollo's own domain: authenticates the key (one credit)."""

        def probe() -> None:
            response = self._send("GET", "/organizations/enrich", [("domain", "apollo.io")])
            if not 200 <= response.status < 300:
                self._raise_for(response)
            self._json(response)

        return self.timed_connection_test(probe)

    def _lookup(self, company: CompanyRef) -> Mapping[str, Any] | None:
        query: list[tuple[str, str]] = []
        if company.domain:
            query.append(("domain", company.domain))
        elif company.website:
            query.append(("website", company.website))
        elif company.profile_url:
            query.append(("linkedin_url", company.profile_url))
        else:
            raise ProviderBadRequest("company", provider_name=self.key)
        if company.name:
            query.append(("name", company.name))
        response = self._send("GET", "/organizations/enrich", query)
        if response.status == 404:
            return None
        if not 200 <= response.status < 300:
            self._raise_for(response)
        org = self._json(response).get("organization")
        if org is None:
            return None
        if not isinstance(org, Mapping):
            raise ProviderUpstreamError(provider_name=self.key)
        return org if _text(org.get("name")) else None

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        """The profile of one company; ``data`` is ``None`` when Apollo does not know it."""
        key = company.domain or company.website or company.profile_url or ""
        provenance = self.provenance(source_ref=f"organizations/enrich:{key}")
        org = self._lookup(company)
        if org is None:
            return AdapterResult(data=None, provenance=provenance, usage=Usage(credits=Decimal(0)))
        return AdapterResult(
            data=self._company(org), provenance=provenance, usage=Usage(credits=Decimal(1))
        )

    def list_job_postings(
        self, company: CompanyRef
    ) -> AdapterResult[tuple[NormalizedJobPosting, ...]]:
        """Open roles (the first page of up to 100). Apollo needs its own organization id, so
        without ``company.provider_id`` one enrichment lookup comes first (one more credit)."""
        credits = Decimal(0)
        organization_id = company.provider_id
        if not organization_id:
            org = self._lookup(company)
            credits += Decimal(1) if org is not None else Decimal(0)
            organization_id = _text(org.get("id")) if org is not None else None
        provenance = self.provenance(source_ref=f"job_postings:{organization_id or 'unknown'}")
        if not organization_id:
            return AdapterResult(data=(), provenance=provenance, usage=Usage(credits=credits))
        response = self._send(
            "GET",
            f"/organizations/{quote(organization_id, safe='')}/job_postings",
            [("page", "1"), ("per_page", str(MAX_PAGE_SIZE))],
        )
        if not 200 <= response.status < 300:
            self._raise_for(response)
        rows = self._json(response).get("organization_job_postings")
        if not isinstance(rows, list):
            raise ProviderUpstreamError(provider_name=self.key)
        postings: list[NormalizedJobPosting] = []
        for row in rows:
            if not isinstance(row, Mapping) or not _text(row.get("title")):
                continue
            place = ", ".join(
                part
                for part in (
                    _text(row.get("city")),
                    _text(row.get("state")),
                    _text(row.get("country")),
                )
                if part
            )
            try:
                postings.append(
                    NormalizedJobPosting(
                        title=_text(row.get("title")) or "",
                        location=place or None,
                        posted_date=_day(row.get("posted_at")),
                        url=_text(row.get("url")),
                        provider_id=_text(row.get("id")),
                    )
                )
            except ValueError:
                continue  # one malformed posting does not spoil the rest
        return AdapterResult(
            data=tuple(postings),
            provenance=provenance,
            usage=Usage(credits=credits + Decimal(1)),
        )

    def search(self, request: CompanySearchRequest) -> AdapterResult[CompanyPage]:
        """One page of companies. The cursor is the next page number."""
        try:
            page = int(request.cursor) if request.cursor else 1
        except ValueError:
            raise ProviderBadRequest("cursor", provider_name=self.key) from None
        if page < 1:
            raise ProviderBadRequest("cursor", provider_name=self.key)
        size = min(request.page_size, MAX_PAGE_SIZE)
        query: list[tuple[str, str]] = [("page", str(page)), ("per_page", str(size))]
        if request.query:
            query.append(("q_organization_name", request.query))
        for code in request.countries:
            name = to_name(code)
            if name is None:
                raise ProviderBadRequest("countries", provider_name=self.key)
            query.append(("organization_locations[]", name))
        for industry in request.industries:
            query.append(("q_organization_keyword_tags[]", industry))
        if request.employee_min is not None or request.employee_max is not None:
            low = request.employee_min if request.employee_min is not None else 1
            high = request.employee_max if request.employee_max is not None else _OPEN_END
            query.append(("organization_num_employees_ranges[]", f"{low},{high}"))
        response = self._send("POST", "/mixed_companies/search", query)
        if not 200 <= response.status < 300:
            self._raise_for(response)
        body = self._json(response)
        rows = body.get("organizations")
        if not isinstance(rows, list):
            raise ProviderUpstreamError(provider_name=self.key)
        excluded = [e.casefold() for e in request.excluded_industries]
        items: list[NormalizedCompany] = []
        for row in rows:
            if not isinstance(row, Mapping) or not _text(row.get("name")):
                continue
            industry = (_text(row.get("industry")) or "").casefold()
            if excluded and any(term in industry for term in excluded):
                continue  # Apollo has no exclusion filter, so it is applied here
            try:
                items.append(self._company(row))
            except ProviderInvalidOutput:
                continue
        pagination = body.get("pagination")
        total_pages = total_entries = None
        if isinstance(pagination, Mapping):
            total_pages = _count(pagination.get("total_pages"))
            total_entries = _count(pagination.get("total_entries"))
        more = total_pages is not None and page < min(total_pages, 500)
        return AdapterResult(
            data=CompanyPage(
                items=tuple(items),
                next_cursor=str(page + 1) if more else None,
                total_estimate=total_entries,
            ),
            provenance=self.provenance(source_ref=f"mixed_companies/search:page{page}"),
            usage=Usage(credits=Decimal(1)),
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


__all__ = ["ApolloAdapter"]
