"""Provider-neutral request and result types for company search (issue #66).

Nothing here names a vendor, calls the network or touches the database.

``CompanySearchRequest``  what to look for. Filters come from the campaign ICP
                           (``from_campaign_rules``), plus free text, paging, sort and ``limit``.
``ProviderCompany``       one company a provider returned, in our vocabulary. ``to_company_input``
                           hands it to the shared #56 validation so provider rows are checked,
                           deduplicated and reported exactly like manual and CSV rows.
``CompanySearchResult``   one page of ``ProviderCompany`` plus paging, provenance and cost.

Paging is cursor based (``cursor`` is opaque to callers; ``next_cursor is None`` means the last
page). ``page_size`` is capped at ``MAX_PAGE_SIZE`` here and again at the provider's own
``capabilities.max_page_size``; ``limit`` caps the total companies one import may pull
(``MAX_LIMIT``). Rate limits, quota and outages are raised as typed errors (``errors.py``);
a page that worked but is incomplete says so in ``warnings`` (partial failure) and the credits it
cost are in ``credits_used``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apps.campaigns.reference import ISO_3166_ALPHA2
from apps.imports.rawdata import sanitize_raw_data
from apps.imports.schema import CompanyInput, InputErrors, validate_company_input

from .errors import ProviderBadRequest
from .keys import try_provider_company_key

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MAX_QUERY_LENGTH = 200
MAX_FILTER_VALUES = 50
MAX_FILTER_VALUE_LENGTH = 100
MAX_CURSOR_LENGTH = 512
MAX_EMPLOYEE_COUNT = 10_000_000

SORT_RELEVANCE = "relevance"
SORT_NAME = "name"
SORT_EMPLOYEES_DESC = "employee_count_desc"
SORT_EMPLOYEES_ASC = "employee_count_asc"
SORT_CHOICES = frozenset({SORT_RELEVANCE, SORT_NAME, SORT_EMPLOYEES_DESC, SORT_EMPLOYEES_ASC})
BUSINESS_MODELS = frozenset({"b2b", "b2c", "both"})

#: Only this version of the campaign rules summary is understood (apps/campaigns/rules_summary).
SUPPORTED_RULES_SCHEMA_VERSION = 1

# ProviderCompany.extra: scalar vendor detail we keep for review, never a whole payload.
EXTRA_MAX_KEYS = 20
EXTRA_MAX_BYTES = 4 * 1024
EXTRA_MAX_VALUE_LENGTH = 500

_TEXT_LIMITS = {
    "name": 300,
    "website": 2000,
    "domain_hint": 255,
    "profile_url": 2000,
    "industry": 200,
    "employee_range": 50,
    "headquarters": 300,
    "description": 500,
    "provider_id": 200,
    "provider_url": 2000,
}


def _clean_list(
    name: str, values: Iterable[str] | None, *, upper: bool = False, iso: bool = False
) -> tuple[str, ...]:
    """Stripped, de-duplicated (case-insensitive, order kept); bad input is a bad request."""
    if values is None:
        return ()
    if isinstance(values, str):
        raise ProviderBadRequest(name)
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ProviderBadRequest(name)
        text = " ".join(value.split())
        if not text:
            continue
        if len(text) > MAX_FILTER_VALUE_LENGTH:
            raise ProviderBadRequest(name)
        if upper:
            text = text.upper()
        if iso and (len(text) != 2 or text not in ISO_3166_ALPHA2):
            raise ProviderBadRequest(name)
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            out.append(text)
    if len(out) > MAX_FILTER_VALUES:
        raise ProviderBadRequest(name)
    return tuple(out)


def _opt_int(name: str, value: object, *, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ProviderBadRequest(name)
    return value


@dataclass(frozen=True)
class CompanySearchRequest:
    """What to search for. Immutable and validated on construction (bad input raises
    ``ProviderBadRequest`` naming the field, so a bad request never reaches a provider).

    ``countries`` are ISO 3166-1 alpha-2 (upper case). ``employee_min``/``employee_max`` are
    inclusive and either may be ``None``. ``business_model`` is ``""``, ``b2b``, ``b2c`` or
    ``both``. A filter left empty means "no restriction". ``limit`` is the most companies the whole
    import will take across pages; ``page_size`` is how many one call returns.
    """

    query: str = ""
    countries: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    employee_min: int | None = None
    employee_max: int | None = None
    business_model: str = ""
    excluded_industries: tuple[str, ...] = ()
    excluded_company_types: tuple[str, ...] = ()
    cursor: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    sort: str = SORT_RELEVANCE
    limit: int = DEFAULT_LIMIT

    def __post_init__(self) -> None:
        def put(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        if not isinstance(self.query, str) or len(self.query.strip()) > MAX_QUERY_LENGTH:
            raise ProviderBadRequest("query")
        put("query", " ".join(self.query.split()))
        put("countries", _clean_list("countries", self.countries, upper=True, iso=True))
        put("industries", _clean_list("industries", self.industries))
        put("excluded_industries", _clean_list("excluded_industries", self.excluded_industries))
        put(
            "excluded_company_types",
            _clean_list("excluded_company_types", self.excluded_company_types),
        )
        lo = _opt_int("employee_min", self.employee_min, minimum=0, maximum=MAX_EMPLOYEE_COUNT)
        hi = _opt_int("employee_max", self.employee_max, minimum=0, maximum=MAX_EMPLOYEE_COUNT)
        if lo is not None and hi is not None and lo > hi:
            raise ProviderBadRequest("employee_max")
        put("employee_min", lo)
        put("employee_max", hi)
        model = self.business_model
        if not isinstance(model, str) or (model.strip().lower() not in BUSINESS_MODELS | {""}):
            raise ProviderBadRequest("business_model")
        put("business_model", model.strip().lower())
        if self.cursor is not None and (
            not isinstance(self.cursor, str)
            or not self.cursor
            or len(self.cursor) > MAX_CURSOR_LENGTH
        ):
            raise ProviderBadRequest("cursor")
        _opt_int("page_size", self.page_size, minimum=1, maximum=MAX_PAGE_SIZE)
        _opt_int("limit", self.limit, minimum=1, maximum=MAX_LIMIT)
        if self.sort not in SORT_CHOICES:
            raise ProviderBadRequest("sort")

    @classmethod
    def from_campaign_rules(
        cls,
        rules_summary: Mapping[str, Any],
        *,
        query: str = "",
        page_size: int = DEFAULT_PAGE_SIZE,
        limit: int = DEFAULT_LIMIT,
        sort: str = SORT_RELEVANCE,
        cursor: str | None = None,
    ) -> CompanySearchRequest:
        """Build the request for a campaign from its rules summary
        (``apps.campaigns.rules_summary.build_rules_summary``, schema version 1).

        Mapping: ``targeting.countries`` -> ``countries``, ``targeting.industries`` ->
        ``industries``, ``targeting.company_size.{min,max}`` -> ``employee_min/max``,
        ``targeting.business_model`` -> ``business_model``, ``exclusions.industries`` and
        ``exclusions.company_types`` -> ``excluded_industries`` / ``excluded_company_types``.
        Missing sections mean "no restriction". An unsupported ``schema_version`` or a malformed
        section raises ``ProviderBadRequest("rules_summary")``.
        """
        if (
            not isinstance(rules_summary, Mapping)
            or rules_summary.get("schema_version") != SUPPORTED_RULES_SCHEMA_VERSION
        ):
            raise ProviderBadRequest("rules_summary")
        targeting = rules_summary.get("targeting", {})
        exclusions = rules_summary.get("exclusions", {})
        if not isinstance(targeting, Mapping) or not isinstance(exclusions, Mapping):
            raise ProviderBadRequest("rules_summary")
        size = targeting.get("company_size", {})
        if not isinstance(size, Mapping):
            raise ProviderBadRequest("rules_summary")
        return cls(
            query=query,
            countries=targeting.get("countries") or (),
            industries=targeting.get("industries") or (),
            employee_min=size.get("min"),
            employee_max=size.get("max"),
            business_model=targeting.get("business_model") or "",
            excluded_industries=exclusions.get("industries") or (),
            excluded_company_types=exclusions.get("company_types") or (),
            cursor=cursor,
            page_size=page_size,
            sort=sort,
            limit=limit,
        )

    def effective_page_size(self, provider_max: int) -> int:
        """The page size to ask a provider for: ours, capped by the provider's own maximum."""
        return max(1, min(self.page_size, provider_max, MAX_PAGE_SIZE))

    def with_cursor(self, cursor: str | None) -> CompanySearchRequest:
        """The same search for the page at ``cursor`` (``None`` is the first page)."""
        return dataclasses.replace(self, cursor=cursor)


def _cap_extra(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep a bounded, credential-free, scalar-only copy of a provider's extra fields."""
    if not raw:
        return {}
    scalars = {
        str(k): v for k, v in raw.items() if v is None or isinstance(v, str | int | float | bool)
    }
    safe = sanitize_raw_data(dict(list(scalars.items())[:EXTRA_MAX_KEYS]))
    out: dict[str, Any] = {}
    for key, value in safe.items():
        if isinstance(value, str):
            value = value[:EXTRA_MAX_VALUE_LENGTH]
        trial = {**out, key: value}
        if len(repr(trial).encode("utf-8")) > EXTRA_MAX_BYTES:
            continue
        out[key] = value
    return out


@dataclass(frozen=True)
class ProviderCompany:
    """One company as a provider describes it, in provider-neutral fields.

    Every field except ``name`` may be blank. ``profile_url`` (for example a LinkedIn company
    page) is an identifier only: it is stored, compared and shown, never fetched or scraped.
    ``country`` should be ISO alpha-2; anything else becomes a ``country_*`` error in
    ``to_company_input``, not a guess. ``provider_id`` is the provider's own record id (the
    idempotency key, see ``keys.py``) and ``provider_url`` its record page (also never fetched).
    ``extra`` is bounded scalar vendor detail (``EXTRA_MAX_KEYS`` keys, ``EXTRA_MAX_BYTES``,
    scrubbed of credentials on construction).
    """

    name: str
    website: str = ""
    domain_hint: str = ""
    profile_url: str = ""
    country: str = ""
    industry: str = ""
    employee_count: int | None = None
    employee_range: str = ""
    headquarters: str = ""
    description: str = ""
    provider_id: str = ""
    provider_url: str = ""
    confidence: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Provider data is untrusted: never raise on it, bound it instead. ``None`` is blank.
        for key, limit in _TEXT_LIMITS.items():
            value = getattr(self, key)
            if value is None:
                value = ""
            if isinstance(value, str):
                # A too-long name/url is kept whole so validation can report it; the rest
                # are display text and are cut.
                if key not in ("name", "website", "profile_url"):
                    value = value[:limit]
                object.__setattr__(self, key, value)
        conf = self.confidence
        if isinstance(conf, bool) or not isinstance(conf, int | float) or not 0 <= conf <= 1:
            object.__setattr__(self, "confidence", None)
        count = self.employee_count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            object.__setattr__(self, "employee_count", None)
        object.__setattr__(self, "extra", _cap_extra(self.extra))

    def provider_key(self, provider_name: str) -> str | None:
        """Idempotency key (``keys.provider_company_key``) or ``None`` without a provider id."""
        return try_provider_company_key(provider_name, self.provider_id)

    def to_company_input(self, *, require_identifier: bool = False) -> CompanyInput | InputErrors:
        """Convert to the shared company input (#56): the same ``validate_company_input`` as
        manual entry and CSV, so provider rows are validated, normalized and deduplicated by the
        same path. Invalid data is returned as ``InputErrors`` (never raised). The website
        falls back to ``domain_hint`` when blank."""
        return validate_company_input(
            {
                "name": self.name,
                "website": self.website or self.domain_hint,
                "profile_url": self.profile_url,
                "country": self.country,
            },
            require_identifier=require_identifier,
        )

    def to_raw_data(self, provider_name: str) -> dict[str, Any]:
        """The payload to keep as ``ImportRow.raw_data``: all provider fields plus the
        ``provider`` name and ``provider_key`` (when there is an id)."""
        data: dict[str, Any] = {
            "provider": provider_name,
            "provider_key": self.provider_key(provider_name),
        }
        for f in dataclasses.fields(self):
            data[f.name] = getattr(self, f.name)
        return sanitize_raw_data(data)


@dataclass(frozen=True)
class CompanySearchResult:
    """One page of search results.

    ``next_cursor`` is ``None`` on the last page. ``total_estimate`` is the provider's own count
    when it gives one (an estimate, may be ``None``). ``raw_ref`` is a provider-side reference
    to the response (a request or trace id) for later lookup in raw payload storage (M3 #80); it
    is not the payload. ``credits_used`` is the cost of this call in the provider's credits when
    known. ``warnings`` are safe short codes/messages for a partial page (for example a skipped
    record), never upstream text.
    """

    items: tuple[ProviderCompany, ...]
    provider_name: str
    retrieved_at: datetime
    total_estimate: int | None = None
    next_cursor: str | None = None
    raw_ref: str = ""
    credits_used: float | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone aware")

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None
