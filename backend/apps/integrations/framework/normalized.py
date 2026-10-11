"""Normalized data types every adapter returns, whatever the vendor (issue #75, ADR 0013).

Rules (Brief sections 5, 17 and 22):

* ``None`` means unknown. An adapter never guesses, defaults or derives a value the provider did
  not give (an empty string is not "unknown"; it is not used either).
* Provider data is untrusted: bad or out-of-range values become ``None`` instead of raising, text
  is stripped and bounded, secrets embedded in free text are masked. The exceptions are the fields
  that make a record meaningless without them (a company or person ``name``, a signal's ``type``,
  evidence and source): those raise ``EnvelopeError`` because the adapter is wrong.
* Evidence-bearing types (``evidence_bearing = True``) must travel in an ``AdapterResult`` whose
  provenance has a source and a retrieval date, see ``envelope.py``.
* Percent changes are ``Decimal`` percent values (``12.5`` is +12.5 percent), never floats.

``NormalizedCompany`` extends the ``ProviderCompany`` semantics of the import contract (#66);
``NormalizedCompany.to_provider_company`` converts for the import flow.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final

from apps.core.logging import redact

from . import _clean
from .errors import EnvelopeError

if TYPE_CHECKING:
    from apps.imports.providers.types import ProviderCompany

# Brief section 7, kept equal to ``apps.research.models.SignalType`` (a test compares them; this
# module stays importable without Django).
SIGNAL_TYPES: Final[frozenset[str]] = frozenset(
    {
        "sales_hiring",
        "bd_hiring",
        "commercial_hiring",
        "partnerships_hiring",
        "headcount_growth",
        "commercial_team_growth",
        "funding",
        "market_expansion",
        "new_leadership",
        "new_office",
        "new_product",
        "major_partnership",
        "other",
    }
)
DEPARTMENTS: Final[frozenset[str]] = frozenset(
    {"sales", "bd", "marketing", "commercial", "partnerships"}
)
SENIORITIES: Final[frozenset[str]] = frozenset(
    {"c_level", "vp", "director", "manager", "senior", "entry", "other"}
)
#: Email deliverability vocabulary shared by enrichment (#84) and verification.
EMAIL_STATUSES: Final[frozenset[str]] = frozenset(
    {"valid", "invalid", "risky", "catch_all", "disposable", "unknown"}
)
QUALITIES: Final[frozenset[str]] = frozenset({"high", "medium", "low"})

MAX_SIGNALS = 50
MAX_DEPARTMENTS = len(DEPARTMENTS)
MAX_PEOPLE = 200
MAX_TEXT = 500
MAX_DESCRIPTION = 2000
MAX_EVIDENCE = 1000
MAX_EXTRA_KEYS = 20
MAX_EXTRA_VALUE = 500
PERCENT_MIN = Decimal("-100")
PERCENT_MAX = Decimal("100000")
MAX_MONEY = Decimal("1e12")
_set = _clean.set_attr


def _quality(value: object) -> str | None:
    return value if isinstance(value, str) and value in QUALITIES else None


def _confidence(value: object) -> Decimal | None:
    return _clean.decimal(value, minimum=Decimal(0), maximum=Decimal(1))


def _percent(value: object) -> Decimal | None:
    return _clean.decimal(value, minimum=PERCENT_MIN, maximum=PERCENT_MAX)


def _extra(raw: Mapping[str, Any] | None) -> dict[str, str | int | bool | None]:
    """Bounded scalar vendor detail, scrubbed of secrets. Floats are kept as text."""
    out: dict[str, str | int | bool | None] = {}
    if not isinstance(raw, Mapping):
        return out
    for key, value in list(raw.items())[:MAX_EXTRA_KEYS]:
        name = _clean.text(str(key), 64)
        if name is None:
            continue
        if isinstance(value, str):
            out[name] = _clean.scrubbed_text(value, MAX_EXTRA_VALUE)
        elif value is None or isinstance(value, bool | int):
            out[name] = value
        elif isinstance(value, float | Decimal):
            out[name] = _clean.text(str(value), 50)
    safe = redact(out)
    return {str(k): v for k, v in safe.items()}


def _require_source(source_url: str | None, source_ref: str | None, what: str) -> None:
    if not source_url and not source_ref:
        raise EnvelopeError(f"{what} needs a source (source_url or source_ref)")


# ------------------------------------------------------------------------------- company
@dataclass(frozen=True)
class DepartmentHeadcount:
    """Headcount of one department and its change over 3, 6 and 12 months (percent)."""

    department: str
    count: int | None = None
    change_3m: Decimal | None = None
    change_6m: Decimal | None = None
    change_12m: Decimal | None = None

    def __post_init__(self) -> None:
        if self.department not in DEPARTMENTS:
            raise EnvelopeError("department must be one of the known departments")
        _set(self, "count", _clean.integer(self.count))
        for name in ("change_3m", "change_6m", "change_12m"):
            _set(self, name, _percent(getattr(self, name)))


@dataclass(frozen=True)
class Funding:
    """Funding facts; every part optional. Amounts are in ``currency`` (ISO 4217, upper case)."""

    total_raised: Decimal | None = None
    last_round_type: str | None = None
    last_round_amount: Decimal | None = None
    last_round_date: date | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        _set(
            self,
            "total_raised",
            _clean.decimal(self.total_raised, minimum=Decimal(0), maximum=MAX_MONEY),
        )
        _set(
            self,
            "last_round_amount",
            _clean.decimal(self.last_round_amount, minimum=Decimal(0), maximum=MAX_MONEY),
        )
        _set(self, "last_round_type", _clean.text(self.last_round_type, 100))
        _set(self, "last_round_date", _clean.day(self.last_round_date))
        _set(self, "currency", _clean.currency(self.currency))


@dataclass(frozen=True)
class SignalCandidate:
    """A trigger a provider reports (Brief section 7), before research validates it.

    ``type`` is one of ``SIGNAL_TYPES``. The evidence text and a source (URL or provider
    reference) are required: a signal without them is not evidence. ``event_date`` is when the
    event happened (``None`` when the provider does not say; ``retrieved_at`` in the envelope
    still dates the observation).
    """

    evidence_bearing: ClassVar[bool] = True

    type: str
    evidence: str
    source_url: str | None = None
    source_ref: str | None = None
    event_date: date | None = None
    confidence: Decimal | None = None

    def __post_init__(self) -> None:
        if self.type not in SIGNAL_TYPES:
            raise EnvelopeError("signal type is not one of the Brief section 7 types")
        evidence = _clean.scrubbed_text(self.evidence, MAX_EVIDENCE)
        if evidence is None:
            raise EnvelopeError("a signal needs evidence text")
        _set(self, "evidence", evidence)
        _set(self, "source_url", _clean.url(self.source_url))
        _set(self, "source_ref", _clean.scrubbed_text(self.source_ref, 200))
        _require_source(self.source_url, self.source_ref, "a signal")
        _set(self, "event_date", _clean.day(self.event_date))
        _set(self, "confidence", _confidence(self.confidence))


@dataclass(frozen=True)
class NormalizedCompany:
    """A company profile in our vocabulary. Only ``name`` is required; the rest is unknown
    (``None``) unless the provider said so. ``profile_url`` (for example a LinkedIn company page)
    is an identifier only: stored and compared, never fetched."""

    evidence_bearing: ClassVar[bool] = True

    name: str
    provider_id: str | None = None
    website: str | None = None
    domain: str | None = None
    profile_url: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2, upper case
    industry: str | None = None
    description: str | None = None
    headquarters: str | None = None
    employee_count: int | None = None
    employee_range: str | None = None
    departments: tuple[DepartmentHeadcount, ...] = ()
    headcount_change_3m: Decimal | None = None
    headcount_change_6m: Decimal | None = None
    headcount_change_12m: Decimal | None = None
    funding: Funding | None = None
    signals: tuple[SignalCandidate, ...] = ()
    confidence: Decimal | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = _clean.text(self.name, 300)
        if name is None:
            raise EnvelopeError("a company needs a name")
        _set(self, "name", name)
        _set(self, "provider_id", _clean.text(self.provider_id, 200))
        _set(self, "website", _clean.url(self.website))
        _set(self, "domain", _clean.domain(self.domain))
        _set(self, "profile_url", _clean.url(self.profile_url))
        _set(self, "country", _clean.country(self.country))
        _set(self, "industry", _clean.text(self.industry, 200))
        _set(self, "description", _clean.scrubbed_text(self.description, MAX_DESCRIPTION))
        _set(self, "headquarters", _clean.text(self.headquarters, 300))
        _set(self, "employee_count", _clean.integer(self.employee_count))
        _set(self, "employee_range", _clean.text(self.employee_range, 50))
        for attr in ("headcount_change_3m", "headcount_change_6m", "headcount_change_12m"):
            _set(self, attr, _percent(getattr(self, attr)))
        departments = tuple(self.departments)
        if not all(isinstance(d, DepartmentHeadcount) for d in departments):
            raise EnvelopeError("departments must be DepartmentHeadcount items")
        if len({d.department for d in departments}) != len(departments):
            raise EnvelopeError("a department may appear once")
        _set(self, "departments", departments)
        if self.funding is not None and not isinstance(self.funding, Funding):
            raise EnvelopeError("funding must be a Funding")
        signals = tuple(self.signals)
        if len(signals) > MAX_SIGNALS or not all(isinstance(s, SignalCandidate) for s in signals):
            raise EnvelopeError("signals must be at most 50 SignalCandidate items")
        _set(self, "signals", signals)
        _set(self, "confidence", _confidence(self.confidence))
        _set(self, "extra", _extra(self.extra))

    def department(self, name: str) -> DepartmentHeadcount | None:
        return next((d for d in self.departments if d.department == name), None)

    def to_provider_company(self) -> ProviderCompany:
        """The #66 import-contract form (blank strings for unknown, as that contract expects)."""
        from apps.imports.providers.types import ProviderCompany

        return ProviderCompany(
            name=self.name,
            website=self.website or "",
            domain_hint=self.domain or "",
            profile_url=self.profile_url or "",
            country=self.country or "",
            industry=self.industry or "",
            employee_count=self.employee_count,
            employee_range=self.employee_range or "",
            headquarters=self.headquarters or "",
            description=self.description or "",
            provider_id=self.provider_id or "",
            confidence=float(self.confidence) if self.confidence is not None else None,
            extra=dict(self.extra),
        )


@dataclass(frozen=True)
class NormalizedJobPosting:
    """An open role (hiring signals, Brief section 7)."""

    evidence_bearing: ClassVar[bool] = True

    title: str
    department: str | None = None
    location: str | None = None
    posted_date: date | None = None
    url: str | None = None
    provider_id: str | None = None

    def __post_init__(self) -> None:
        title = _clean.text(self.title, 300)
        if title is None:
            raise EnvelopeError("a job posting needs a title")
        _set(self, "title", title)
        _set(self, "department", self.department if self.department in DEPARTMENTS else None)
        _set(self, "location", _clean.text(self.location, 300))
        _set(self, "posted_date", _clean.day(self.posted_date))
        _set(self, "url", _clean.url(self.url))
        _set(self, "provider_id", _clean.text(self.provider_id, 200))


# --------------------------------------------------------------------------------- people
@dataclass(frozen=True)
class NormalizedPerson:
    """A person at a company. Personal data: keep only what a feature needs (adapter
    requirements, section on personal data). ``email_status`` says how far ``email`` is trusted;
    ``"unknown"`` until a verifier says otherwise."""

    evidence_bearing: ClassVar[bool] = True

    name: str
    title: str | None = None
    profile_url: str | None = None
    email: str | None = None
    email_status: str = "unknown"
    company_domain: str | None = None
    department: str | None = None
    seniority: str | None = None
    provider_id: str | None = None

    def __post_init__(self) -> None:
        name = _clean.text(self.name, 200)
        if name is None:
            raise EnvelopeError("a person needs a name")
        _set(self, "name", name)
        _set(self, "title", _clean.text(self.title, 200))
        _set(self, "profile_url", _clean.url(self.profile_url))
        _set(self, "email", _clean.email(self.email))
        _set(self, "email_status", _status(self.email_status))
        _set(self, "company_domain", _clean.domain(self.company_domain))
        _set(self, "department", self.department if self.department in DEPARTMENTS else None)
        _set(self, "seniority", self.seniority if self.seniority in SENIORITIES else None)
        _set(self, "provider_id", _clean.text(self.provider_id, 200))


def _status(value: object) -> str:
    return value if isinstance(value, str) and value in EMAIL_STATUSES else "unknown"


@dataclass(frozen=True)
class EmailFinding:
    """What an email enrichment provider found for one person: ``email`` is ``None`` when it
    found nothing (a normal answer, not an error)."""

    evidence_bearing: ClassVar[bool] = True

    email: str | None = None
    status: str = "unknown"
    confidence: Decimal | None = None

    def __post_init__(self) -> None:
        _set(self, "email", _clean.email(self.email))
        _set(self, "status", _status(self.status))
        _set(self, "confidence", _confidence(self.confidence))


@dataclass(frozen=True)
class EmailVerification:
    """The verdict of an email verification provider (Brief section 17). The flags are
    ``None`` when the provider did not check them."""

    evidence_bearing: ClassVar[bool] = True

    email: str
    status: str = "unknown"
    reason: str | None = None
    is_role_account: bool | None = None
    is_disposable: bool | None = None
    is_catch_all: bool | None = None
    mx_found: bool | None = None

    def __post_init__(self) -> None:
        address = _clean.email(self.email)
        if address is None:
            raise EnvelopeError("a verification needs a well formed email")
        _set(self, "email", address)
        _set(self, "status", _status(self.status))
        _set(self, "reason", _clean.scrubbed_text(self.reason, 200))
        for flag in ("is_role_account", "is_disposable", "is_catch_all", "mx_found"):
            value = getattr(self, flag)
            _set(self, flag, value if isinstance(value, bool) else None)

    @property
    def deliverable(self) -> bool | None:
        """``True`` valid, ``False`` invalid or disposable, ``None`` when it is not certain."""
        if self.status == "valid":
            return True
        if self.status in ("invalid", "disposable"):
            return False
        return None


# ---------------------------------------------------------------------- request references
@dataclass(frozen=True)
class CompanyRef:
    """How to identify a company in a request. At least one identifier is required."""

    name: str | None = None
    domain: str | None = None
    website: str | None = None
    profile_url: str | None = None
    provider_id: str | None = None

    def __post_init__(self) -> None:
        _set(self, "name", _clean.text(self.name, 300))
        _set(self, "domain", _clean.domain(self.domain))
        _set(self, "website", _clean.url(self.website))
        _set(self, "profile_url", _clean.url(self.profile_url))
        _set(self, "provider_id", _clean.text(self.provider_id, 200))
        if not any((self.name, self.domain, self.website, self.profile_url, self.provider_id)):
            raise EnvelopeError("a company reference needs at least one identifier")


@dataclass(frozen=True)
class PersonRef:
    """How to identify a person for email lookup: a name plus, ideally, a profile URL."""

    full_name: str
    title: str | None = None
    profile_url: str | None = None

    def __post_init__(self) -> None:
        name = _clean.text(self.full_name, 200)
        if name is None:
            raise EnvelopeError("a person reference needs a name")
        _set(self, "full_name", name)
        _set(self, "title", _clean.text(self.title, 200))
        _set(self, "profile_url", _clean.url(self.profile_url))


@dataclass(frozen=True)
class CompanyPage:
    """One page of company search results (the data of ``CompanyDataAdapter.search``)."""

    evidence_bearing: ClassVar[bool] = True

    items: tuple[NormalizedCompany, ...] = ()
    next_cursor: str | None = None
    total_estimate: int | None = None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if not all(isinstance(c, NormalizedCompany) for c in items):
            raise EnvelopeError("a page holds NormalizedCompany items")
        _set(self, "items", items)
        _set(self, "next_cursor", _clean.text(self.next_cursor, 512))
        _set(self, "total_estimate", _clean.integer(self.total_estimate, maximum=1_000_000_000))

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def iter_evidence(data: object) -> Iterable[object]:
    """Yield the evidence-bearing objects inside ``data`` (one level of tuple/list/page)."""
    stack: list[object] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, tuple | list):
            stack.extend(item)
        elif getattr(item, "evidence_bearing", False):
            yield item
