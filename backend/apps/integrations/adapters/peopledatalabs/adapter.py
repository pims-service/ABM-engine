"""People Data Labs company-data adapter: enrichment and search with headcount by department,
growth over 3, 6 and 12 months, and funding.

Checked against People Data Labs' own API documentation (https://docs.peopledatalabs.com) on
2026-11-01:

* Base ``https://api.peopledatalabs.com/v5``. The key goes in the ``X-Api-Key`` header (the docs
  also allow an ``api_key`` parameter; the header keeps the key out of URLs).
* Company Enrichment: ``GET /company/enrich`` with one of ``pdl_id``, ``website``, ``profile``,
  ``name`` or ``ticker``, plus ``min_likelihood`` (1 to 10) and ``titlecase``. ``200`` answers the
  company fields at the top level next to ``status`` and ``likelihood``; ``404`` means no match.
  "We charge per match", so a ``404`` is taken as free. 10 requests a minute on the free plan,
  1,000 on a paid one.
* Company Search: ``POST /company/search`` with a JSON body holding an Elasticsearch ``query``
  (or ``sql``), ``size`` (1 to 100), ``scroll_token`` and ``titlecase``. Answers ``{"status",
  "data": [...], "total", "scroll_token"}``; a search with no match is still ``200``. One credit
  per record returned.
* Company fields used: ``id``, ``name``, ``display_name``, ``website``, ``linkedin_url``,
  ``industry``, ``summary``, ``size``, ``employee_count``, ``location.name`` and
  ``location.country`` (a lower case name), ``employee_count_by_role`` (for example ``sales``,
  ``marketing`` and ``partnerships``), ``employee_count_by_month`` (``{"2026-09": 88, ...}``),
  ``total_funding_raised``, ``latest_funding_stage``, ``last_funding_date`` and
  ``funding_details`` (``funding_raised``, ``funding_currency``, ``funding_round_date``).

Growth: People Data Labs gives no growth percentage, only the monthly headcount series, so this
adapter works the 3, 6 and 12 month change out of that series (the latest month against the
month 3, 6 or 12 months before it). A window whose month is missing stays unknown. There is no
business development or commercial headcount in its role list, and no job posting list (only
counts), so those are not offered.

NOT verified against a live key (none was available): the exact role and stage names beyond the
docs' example record, how the ``min_likelihood`` default behaves for name-only lookups, and the
field names the search query accepts for countries and industries (they are the same names as in
the record). The connection test looks up a company that does not exist: a valid key answers
``404`` (free), a bad one ``401``.

The ``Transport`` is injectable; tests never use the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar
from urllib.parse import urlencode, urlsplit

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
    HEADCOUNT_GROWTH_3M,
    HEADCOUNT_GROWTH_6M,
    HEADCOUNT_GROWTH_12M,
    SEARCH,
)
from apps.integrations.framework.normalized import DepartmentHeadcount, Funding

API_HOST = "api.peopledatalabs.com"
DEFAULT_API_BASE = f"https://{API_HOST}/v5"
MAX_PAGE_SIZE = 100
#: The provider's role names, in the app's department vocabulary. Its list has no BD or
#: commercial role.
_DEPARTMENTS = {"sales": "sales", "marketing": "marketing", "partnerships": "partnerships"}
_NO_SUCH_COMPANY = "no-such-company-abm-connection-test.com"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _money(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = Decimal(str(value))
    return number if number.is_finite() and number >= 0 else None


def _day(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _month_index(key: str) -> int | None:
    """``"2026-09"`` -> a number that grows by one a month; ``None`` for anything else."""
    parts = key.split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    year, month = int(parts[0]), int(parts[1])
    return year * 12 + month - 1 if 1 <= month <= 12 else None


def growth_percent(series: object, months: int) -> Decimal | None:
    """The percent change of the latest month's headcount against ``months`` months before it,
    from a ``{"YYYY-MM": count}`` series. ``None`` when either month is missing or the earlier
    count is zero."""
    if not isinstance(series, Mapping):
        return None
    counts: dict[int, int] = {}
    for key, value in series.items():
        index = _month_index(key) if isinstance(key, str) else None
        count = _count(value)
        if index is not None and count is not None:
            counts[index] = count
    if not counts:
        return None
    latest = max(counts)
    earlier = counts.get(latest - months)
    if not earlier:
        return None
    change = (Decimal(counts[latest]) - Decimal(earlier)) * 100 / Decimal(earlier)
    return change.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _url(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text if text.startswith(("http://", "https://")) else f"https://{text}"


class PeopleDataLabsAdapter(CompanyDataAdapter):
    """Company enrichment and search from People Data Labs."""

    key = "peopledatalabs"
    display_name = "People Data Labs"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset(
            {
                SEARCH,
                ENRICH_COMPANY,
                DEPARTMENT_HEADCOUNT,
                HEADCOUNT_GROWTH_3M,
                HEADCOUNT_GROWTH_6M,
                HEADCOUNT_GROWTH_12M,
                FUNDING,
            }
        ),
        requires_credentials=True,
        max_page_size=MAX_PAGE_SIZE,
        allowed_hosts=(API_HOST,),
        test_connection_costs_credits=False,
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
            "min_likelihood": {
                "type": "integer",
                "default": 6,
                "minimum": 1,
                "maximum": 10,
                "description": "How sure a match must be (1 to 10). Lower finds more companies "
                "and risks a wrong one.",
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
    def _send(
        self,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        url = str(self.config["api_base"]).rstrip("/") + path
        if query:
            url += "?" + urlencode(query)
        headers = {"x-api-key": self.credential("api_key").reveal(), "accept": "application/json"}
        payload: bytes | None = None
        if body is not None:
            headers["content-type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        request = HttpRequest(
            method, url, headers, payload, timeout=float(self.config["timeout_seconds"])
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
    def _company(
        self, record: Mapping[str, Any], likelihood: int | None = None
    ) -> NormalizedCompany:
        departments = []
        by_role = record.get("employee_count_by_role")
        if isinstance(by_role, Mapping):
            for role, department in _DEPARTMENTS.items():
                count = _count(by_role.get(role))
                if count is not None:
                    departments.append(DepartmentHeadcount(department, count))
        location = record.get("location")
        place = country = None
        if isinstance(location, Mapping):
            place = _text(location.get("name"))
            country = to_iso(location.get("country"))
        series = record.get("employee_count_by_month")
        website = _text(record.get("website"))
        try:
            return NormalizedCompany(
                name=_text(record.get("display_name")) or _text(record.get("name")) or "",
                provider_id=_text(record.get("id")),
                website=_url(website),
                domain=website,
                profile_url=_url(record.get("linkedin_url")),
                country=country,
                industry=_text(record.get("industry")),
                description=_text(record.get("summary")),
                headquarters=place,
                employee_count=_count(record.get("employee_count")),
                employee_range=_text(record.get("size")),
                departments=tuple(departments),
                headcount_change_3m=growth_percent(series, 3),
                headcount_change_6m=growth_percent(series, 6),
                headcount_change_12m=growth_percent(series, 12),
                funding=self._funding(record),
                confidence=Decimal(likelihood) / 10 if likelihood is not None else None,
                extra={"likelihood": likelihood} if likelihood is not None else {},
            )
        except ValueError:
            raise ProviderInvalidOutput(provider_name=self.key) from None

    @staticmethod
    def _funding(record: Mapping[str, Any]) -> Funding | None:
        total = _money(record.get("total_funding_raised"))
        stage = _text(record.get("latest_funding_stage"))
        when = _day(record.get("last_funding_date"))
        amount: Decimal | None = None
        currency: str | None = None
        rounds = record.get("funding_details")
        if isinstance(rounds, list):
            dated = [
                r for r in rounds if isinstance(r, Mapping) and _day(r.get("funding_round_date"))
            ]
            if dated:
                newest = max(dated, key=lambda r: _day(r.get("funding_round_date")) or date.min)
                amount = _money(newest.get("funding_raised"))
                currency = _text(newest.get("funding_currency"))
        if total is None and stage is None and when is None and amount is None:
            return None
        return Funding(
            total_raised=total,
            last_round_type=stage.replace("_", " ").title() if stage else None,
            last_round_amount=amount,
            last_round_date=when,
            currency=currency,
        )

    # ------------------------------------------------------------------ operations
    def test_connection(self) -> ConnectionTestResult:
        """Look up a company that does not exist: a valid key answers ``404`` (no match, no
        charge), a bad key ``401``."""

        def probe() -> None:
            response = self._send("GET", "/company/enrich", {"website": _NO_SUCH_COMPANY})
            if response.status not in (200, 404):
                self._raise_for(response)

        return self.timed_connection_test(probe)

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        """The profile of one company; ``data`` is ``None`` when there is no match."""
        query: dict[str, str] = {
            "min_likelihood": str(self.config["min_likelihood"]),
            "titlecase": "true",
        }
        if company.provider_id:
            query["pdl_id"] = company.provider_id
        elif company.domain:
            query["website"] = company.domain
        elif company.website:
            query["website"] = company.website
        elif company.profile_url:
            query["profile"] = company.profile_url
        elif company.name:
            query["name"] = company.name
        else:
            raise ProviderBadRequest("company", provider_name=self.key)
        reference = next(
            v for k, v in query.items() if k in ("pdl_id", "website", "profile", "name")
        )
        provenance = self.provenance(source_ref=f"company/enrich:{reference}")
        response = self._send("GET", "/company/enrich", query)
        if response.status == 404:
            return AdapterResult(data=None, provenance=provenance, usage=Usage(credits=Decimal(0)))
        if not 200 <= response.status < 300:
            self._raise_for(response)
        record = self._json(response)
        if not _text(record.get("name")) and not _text(record.get("display_name")):
            return AdapterResult(data=None, provenance=provenance, usage=Usage(credits=Decimal(0)))
        likelihood = record.get("likelihood")
        return AdapterResult(
            data=self._company(
                record,
                likelihood if isinstance(likelihood, int) and 1 <= likelihood <= 10 else None,
            ),
            provenance=provenance,
            usage=Usage(credits=Decimal(1)),
        )

    def search(self, request: CompanySearchRequest) -> AdapterResult[CompanyPage]:
        """One page of companies. The cursor is People Data Labs' ``scroll_token``. Every record
        returned is billed, so an unfiltered search still costs up to ``page_size`` credits."""
        must: list[dict[str, Any]] = []
        if request.query:
            must.append({"match": {"name": request.query}})
        if request.countries:
            names = []
            for code in request.countries:
                name = to_name(code)
                if name is None:
                    raise ProviderBadRequest("countries", provider_name=self.key)
                names.append(name.casefold())
            must.append({"terms": {"location.country": names}})
        if request.industries:
            must.append({"terms": {"industry": [i.casefold() for i in request.industries]}})
        if request.employee_min is not None or request.employee_max is not None:
            bounds: dict[str, int] = {}
            if request.employee_min is not None:
                bounds["gte"] = request.employee_min
            if request.employee_max is not None:
                bounds["lte"] = request.employee_max
            must.append({"range": {"employee_count": bounds}})
        if not must:
            must.append({"match_all": {}})  # billed per record: a page costs up to page_size
        bool_query: dict[str, Any] = {"must": must}
        if request.excluded_industries:
            bool_query["must_not"] = [
                {"terms": {"industry": [i.casefold() for i in request.excluded_industries]}}
            ]
        size = min(request.page_size, MAX_PAGE_SIZE)
        body: dict[str, Any] = {"query": {"bool": bool_query}, "size": size, "titlecase": True}
        if request.cursor:
            body["scroll_token"] = request.cursor
        response = self._send("POST", "/company/search", body=body)
        if not 200 <= response.status < 300:
            self._raise_for(response)
        data = self._json(response)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderUpstreamError(provider_name=self.key)
        items: list[NormalizedCompany] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                items.append(self._company(row))
            except ProviderInvalidOutput:
                continue
        token = _text(data.get("scroll_token"))
        total = _count(data.get("total"))
        return AdapterResult(
            data=CompanyPage(
                items=tuple(items),
                next_cursor=token if token and len(rows) >= size else None,
                total_estimate=total,
            ),
            provenance=self.provenance(source_ref="company/search"),
            usage=Usage(credits=Decimal(len(rows))),
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


__all__ = ["API_HOST", "PeopleDataLabsAdapter", "growth_percent"]
