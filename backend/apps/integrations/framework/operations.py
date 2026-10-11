"""Per-category adapter base classes with their typed operations (issue #74, ADR 0013).

Each category base class extends ``Adapter`` and defines the operations of its category. An
operation an adapter does not implement raises ``ProviderBadRequest(<operation>)`` by default and
is left out of ``capabilities.features``: callers check ``supports(adapter, feature)`` first, and
``execute`` refuses an operation whose feature is not declared.

    CompanyDataAdapter    search, enrich_company, list_job_postings
    PeopleDataAdapter     find_people
    EmailEnrichmentAdapter  find_email
    EmailVerificationAdapter  verify
    LLMAdapter            generate_structured
    CRMAdapter            push_contact, push_company (idempotent upserts)
    OutreachAdapter       export_prospects (export only in V1; no sending)
    StorageAdapter        put_object, get_object, delete_object
    AutomationAdapter     trigger, run_status

Every operation returns ``AdapterResult`` (``envelope.py``). Call operations through
``execute(adapter, adapter.operation, ...)`` so only typed errors escape and the result is checked.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Final

from . import _clean
from .base import Adapter
from .categories import (
    DELETE_OBJECT,
    ENRICH_COMPANY,
    EXPORT,
    FIND_EMAIL,
    FIND_PEOPLE,
    GET_OBJECT,
    JOB_POSTINGS,
    PUSH_COMPANY,
    PUSH_CONTACT,
    PUT_OBJECT,
    RUN_STATUS,
    SEARCH,
    STRUCTURED_OUTPUT,
    TRIGGER,
    VERIFY_EMAIL,
    IntegrationCategory,
)
from .envelope import AdapterResult
from .errors import (
    EnvelopeError,
    ProviderBadRequest,
    ProviderError,
    ProviderInvalidOutput,
    ProviderUnavailable,
    ProviderUpstreamError,
)
from .normalized import (
    CompanyPage,
    CompanyRef,
    EmailFinding,
    EmailVerification,
    NormalizedCompany,
    NormalizedJobPosting,
    NormalizedPerson,
    PersonRef,
)

if TYPE_CHECKING:
    from apps.imports.providers.types import CompanySearchRequest

MAX_ROLES = 20
MAX_RECIPIENTS = 1000
MAX_OBJECT_BYTES = 10 * 1024 * 1024
MAX_PAYLOAD_KEYS = 50
MAX_FIELDS = 50
_STORAGE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.=-]{0,254}")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_./=-]{0,199}")
_set = _clean.set_attr


def _unsupported(operation: str) -> ProviderBadRequest:
    return ProviderBadRequest(operation)


def _fields(raw: Mapping[str, Any] | None) -> Mapping[str, str]:
    """Bounded string fields for CRM/outreach payloads, scrubbed of secrets."""
    out: dict[str, str] = {}
    for key, value in list((raw or {}).items())[:MAX_FIELDS]:
        name = _clean.text(str(key), 64)
        val = _clean.scrubbed_text(value if isinstance(value, str) else str(value), 500)
        if name and val:
            out[name] = val
    return out


def idempotency_key(value: object) -> str:
    """A caller-chosen stable key (for example ``"<client>:<company id>"``). The same key must
    never create a second record at the provider."""
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY.fullmatch(value):
        raise ProviderBadRequest("idempotency_key")
    return value


# ------------------------------------------------------------------------- company data
class CompanyDataAdapter(Adapter):
    """Company search and enrichment (#82). Feature names: ``search``, ``enrich_company``,
    ``job_postings``, ``department_headcount``, ``headcount_growth_3m/6m/12m``, ``funding``,
    ``signals``: the last five say which optional fields of ``NormalizedCompany`` the adapter
    can fill (an undeclared field must stay ``None``)."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.COMPANY_DATA

    def search(self, request: CompanySearchRequest) -> AdapterResult[CompanyPage]:
        """One page for ``request`` (cursor paging, same request type as the import contract
        #66). At most ``capabilities.max_page_size`` items."""
        raise _unsupported("search")

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        """The profile of one company; ``data`` is ``None`` when the provider has none."""
        raise _unsupported("enrich_company")

    def list_job_postings(
        self, company: CompanyRef
    ) -> AdapterResult[tuple[NormalizedJobPosting, ...]]:
        raise _unsupported("list_job_postings")


# ------------------------------------------------------------------------- people data
class PeopleDataAdapter(Adapter):
    """People at a company by role (#83)."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.PEOPLE_DATA

    def find_people(
        self, company: CompanyRef, roles: Sequence[str], *, limit: int = 10
    ) -> AdapterResult[tuple[NormalizedPerson, ...]]:
        """People at ``company`` whose title matches one of ``roles`` (for example
        ``"head of sales"``), at most ``limit``. Emails are optional and unverified."""
        raise _unsupported("find_people")


def clean_roles(roles: Sequence[str]) -> tuple[str, ...]:
    """Validated role strings (1 to ``MAX_ROLES``, each 1-100 chars); bad input is a bad request."""
    if isinstance(roles, str) or not roles or len(roles) > MAX_ROLES:
        raise ProviderBadRequest("roles")
    out = [_clean.text(r, 100) for r in roles]
    if not all(out):
        raise ProviderBadRequest("roles")
    return tuple(r for r in out if r)


# ------------------------------------------------------------------- email enrichment
class EmailEnrichmentAdapter(Adapter):
    """Find a work email for a known person (#84). Personal data: call it only for people the
    campaign actually targets."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.EMAIL_ENRICHMENT

    def find_email(self, person: PersonRef, domain: str) -> AdapterResult[EmailFinding]:
        """The most likely email of ``person`` at ``domain``; ``data.email is None`` if none."""
        raise _unsupported("find_email")


class EmailVerificationAdapter(Adapter):
    """Check whether an address is deliverable (#84)."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.EMAIL_VERIFICATION

    def verify(self, email: str) -> AdapterResult[EmailVerification]:
        raise _unsupported("verify")


# ------------------------------------------------------------------------------- LLM
@dataclass(frozen=True)
class PromptSpec:
    """A versioned prompt (#81): ``name`` and ``version`` are stored with each call."""

    name: str
    version: str
    user: str
    system: str = ""

    def __post_init__(self) -> None:
        for attr, limit in (("name", 100), ("version", 50)):
            value = _clean.text(getattr(self, attr), limit)
            if value is None:
                raise ProviderBadRequest("prompt")
            _set(self, attr, value)
        if not isinstance(self.user, str) or not self.user.strip() or len(self.user) > 200_000:
            raise ProviderBadRequest("prompt")
        if not isinstance(self.system, str) or len(self.system) > 50_000:
            raise ProviderBadRequest("prompt")


@dataclass(frozen=True)
class StructuredSchema[T]:
    """What the model must return (Brief section 18, ADR 0003): a ``json_schema`` sent to the
    provider and a ``validate`` callable that turns the decoded JSON into the typed object or
    raises. With Pydantic: ``StructuredSchema("icp", Model.model_json_schema(),
    Model.model_validate)``. The framework itself needs no Pydantic."""

    name: str
    json_schema: Mapping[str, Any]
    validate: Callable[[Any], T]

    def parse(self, raw: Any, *, provider_name: str = "") -> T:
        """Validate model output. Any failure becomes ``ProviderInvalidOutput``; the validator's
        message (it may echo model text) is not kept."""
        try:
            return self.validate(raw)
        except Exception:
            raise ProviderInvalidOutput(provider_name=provider_name) from None


class LLMAdapter(Adapter):
    """Structured generation from a language model (#81). Free text is not an interface:
    application logic gets validated objects."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.LLM

    def generate_structured[T](
        self,
        prompt: PromptSpec,
        schema: StructuredSchema[T],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AdapterResult[T]:
        """Run ``prompt`` and return the output validated by ``schema``; ``usage`` carries the
        model name and token counts. Invalid output raises ``ProviderInvalidOutput``."""
        raise _unsupported("generate_structured")


# ------------------------------------------------------------------------------- CRM
@dataclass(frozen=True)
class CRMContact:
    """A contact to push. ``external_key`` is OUR stable id (for example ``"contact:42"``): the
    same key must update the same CRM record, never create a duplicate."""

    external_key: str
    name: str
    email: str | None = None
    title: str | None = None
    company_domain: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set(self, "external_key", idempotency_key(self.external_key))
        name = _clean.text(self.name, 200)
        if name is None:
            raise ProviderBadRequest("name")
        _set(self, "name", name)
        _set(self, "email", _clean.email(self.email))
        _set(self, "title", _clean.text(self.title, 200))
        _set(self, "company_domain", _clean.domain(self.company_domain))
        _set(self, "fields", _fields(self.fields))


@dataclass(frozen=True)
class CRMCompany:
    external_key: str
    name: str
    domain: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set(self, "external_key", idempotency_key(self.external_key))
        name = _clean.text(self.name, 300)
        if name is None:
            raise ProviderBadRequest("name")
        _set(self, "name", name)
        _set(self, "domain", _clean.domain(self.domain))
        _set(self, "fields", _fields(self.fields))


CRM_ACTIONS: Final = frozenset({"created", "updated", "unchanged"})


@dataclass(frozen=True)
class CRMPushResult:
    external_key: str
    remote_id: str
    action: str  # created | updated | unchanged

    def __post_init__(self) -> None:
        remote = _clean.text(self.remote_id, 200)
        if remote is None or self.action not in CRM_ACTIONS:
            raise EnvelopeError("a CRM push result needs remote_id and a known action")
        _set(self, "remote_id", remote)


class CRMAdapter(Adapter):
    """Push contacts and companies into a CRM (#85, GoHighLevel first). Both operations are
    idempotent upserts keyed by ``external_key``: pushing the same key twice updates (or leaves
    unchanged) one record. V1 pushes only; it does not read back or sync."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.CRM

    def push_contact(self, contact: CRMContact) -> AdapterResult[CRMPushResult]:
        raise _unsupported("push_contact")

    def push_company(self, company: CRMCompany) -> AdapterResult[CRMPushResult]:
        raise _unsupported("push_company")


# ------------------------------------------------------------------------- outreach
@dataclass(frozen=True)
class OutreachRecipient:
    external_key: str
    name: str
    email: str
    company: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set(self, "external_key", idempotency_key(self.external_key))
        name = _clean.text(self.name, 200)
        address = _clean.email(self.email)
        if name is None or address is None:
            raise ProviderBadRequest("recipient")
        _set(self, "name", name)
        _set(self, "email", address)
        _set(self, "company", _clean.text(self.company, 300))
        _set(self, "fields", _fields(self.fields))


@dataclass(frozen=True)
class OutreachExport:
    """A list of approved recipients to hand to an outreach platform. A human has approved
    the content; the adapter exports, it does not send (Brief section 17, V1)."""

    list_name: str
    recipients: tuple[OutreachRecipient, ...]

    def __post_init__(self) -> None:
        name = _clean.text(self.list_name, 200)
        recipients = tuple(self.recipients)
        if (
            name is None
            or not recipients
            or len(recipients) > MAX_RECIPIENTS
            or not all(isinstance(r, OutreachRecipient) for r in recipients)
        ):
            raise ProviderBadRequest("recipients")
        _set(self, "list_name", name)
        _set(self, "recipients", recipients)


@dataclass(frozen=True)
class ExportReceipt:
    accepted: int
    rejected: int = 0
    remote_ref: str | None = None

    def __post_init__(self) -> None:
        accepted = _clean.integer(self.accepted, maximum=MAX_RECIPIENTS)
        rejected = _clean.integer(self.rejected, maximum=MAX_RECIPIENTS)
        if accepted is None or rejected is None:
            raise EnvelopeError("an export receipt needs counts")
        _set(self, "accepted", accepted)
        _set(self, "rejected", rejected)
        _set(self, "remote_ref", _clean.scrubbed_text(self.remote_ref, 200))


class OutreachAdapter(Adapter):
    """Export prospects to an outreach platform (V1: export only, no sending)."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.OUTREACH

    def export_prospects(self, export: OutreachExport) -> AdapterResult[ExportReceipt]:
        raise _unsupported("export_prospects")


# -------------------------------------------------------------------------- storage
@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    content_type: str | None = None
    etag: str | None = None

    def __post_init__(self) -> None:
        size = _clean.integer(self.size, maximum=MAX_OBJECT_BYTES)
        if size is None:
            raise EnvelopeError("a stored object needs a size within the cap")
        _set(self, "key", storage_key(self.key))
        _set(self, "size", size)
        _set(self, "content_type", _clean.text(self.content_type, 100))
        _set(self, "etag", _clean.text(self.etag, 200))


def storage_key(value: object) -> str:
    """A safe object key: letters, digits, ``/ _ . = -``, no ``..`` segment, at most 255."""
    if (
        not isinstance(value, str)
        or not _STORAGE_KEY.fullmatch(value)
        or ".." in value.split("/")
        or "//" in value
    ):
        raise ProviderBadRequest("key")
    return value


class StorageAdapter(Adapter):
    """Object storage for raw payloads and exports (#80). Keys are ours; content is bytes capped
    at ``MAX_OBJECT_BYTES``."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.STORAGE

    def put_object(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> AdapterResult[StoredObject]:
        raise _unsupported("put_object")

    def get_object(self, key: str) -> AdapterResult[bytes]:
        """The bytes; a missing key raises ``ProviderNotFound``."""
        raise _unsupported("get_object")

    def delete_object(self, key: str) -> AdapterResult[bool]:
        """``True`` if something was deleted, ``False`` if the key did not exist."""
        raise _unsupported("delete_object")


# ------------------------------------------------------------------------ automation
RUN_STATUSES: Final = frozenset({"queued", "running", "succeeded", "failed"})


@dataclass(frozen=True)
class AutomationRun:
    run_id: str
    status: str  # queued | running | succeeded | failed

    def __post_init__(self) -> None:
        run_id = _clean.text(self.run_id, 200)
        if run_id is None or self.status not in RUN_STATUSES:
            raise EnvelopeError("an automation run needs an id and a known status")
        _set(self, "run_id", run_id)


class AutomationAdapter(Adapter):
    """Start external automations (n8n, Make style) and read their status. The automation is
    a vendor behind this adapter, not part of the framework (ADR 0002)."""

    category: ClassVar[IntegrationCategory] = IntegrationCategory.AUTOMATION

    def trigger(
        self, workflow: str, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> AdapterResult[AutomationRun]:
        """Start ``workflow``; the same ``idempotency_key`` must not start a second run."""
        raise _unsupported("trigger")

    def run_status(self, run_id: str) -> AdapterResult[AutomationRun]:
        raise _unsupported("run_status")


def clean_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bounded, secret-scrubbed copy of an automation payload."""
    from apps.imports.rawdata import sanitize_raw_data

    if not isinstance(payload, Mapping) or len(payload) > MAX_PAYLOAD_KEYS:
        raise ProviderBadRequest("payload")
    return sanitize_raw_data(dict(payload))


#: Adapter base class of each category.
CATEGORY_BASES: Final[dict[IntegrationCategory, type[Any]]] = {
    IntegrationCategory.LLM: LLMAdapter,
    IntegrationCategory.COMPANY_DATA: CompanyDataAdapter,
    IntegrationCategory.PEOPLE_DATA: PeopleDataAdapter,
    IntegrationCategory.EMAIL_ENRICHMENT: EmailEnrichmentAdapter,
    IntegrationCategory.EMAIL_VERIFICATION: EmailVerificationAdapter,
    IntegrationCategory.CRM: CRMAdapter,
    IntegrationCategory.OUTREACH: OutreachAdapter,
    IntegrationCategory.STORAGE: StorageAdapter,
    IntegrationCategory.AUTOMATION: AutomationAdapter,
}

#: Operation method name -> the feature that must be declared to call it.
OPERATION_FEATURES: Final[dict[str, str]] = {
    "search": SEARCH,
    "enrich_company": ENRICH_COMPANY,
    "list_job_postings": JOB_POSTINGS,
    "find_people": FIND_PEOPLE,
    "find_email": FIND_EMAIL,
    "verify": VERIFY_EMAIL,
    "generate_structured": STRUCTURED_OUTPUT,
    "push_contact": PUSH_CONTACT,
    "push_company": PUSH_COMPANY,
    "export_prospects": EXPORT,
    "put_object": PUT_OBJECT,
    "get_object": GET_OBJECT,
    "delete_object": DELETE_OBJECT,
    "trigger": TRIGGER,
    "run_status": RUN_STATUS,
}


def execute[**P, T](
    adapter: Adapter,
    operation: Callable[P, AdapterResult[T]],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> AdapterResult[T]:
    """Run one adapter operation with the contract enforced (the generic ``execute_search``).

    * refuses an operation whose feature the adapter did not declare (``ProviderBadRequest``);
    * lets ``ProviderError`` through, turns a broken envelope into ``ProviderUpstreamError`` and
      any other exception into ``ProviderUnavailable`` (the original stays in ``__cause__``);
    * checks the result is an ``AdapterResult`` stamped with this adapter and category;
    * fills ``usage.latency_ms`` (measured here) when the adapter left it out.
    """
    name = getattr(operation, "__name__", "")
    feature = OPERATION_FEATURES.get(name)
    if feature is not None and not adapter.capabilities.supports(feature):
        raise ProviderBadRequest(name, provider_name=adapter.key)
    start = time.perf_counter()
    try:
        result = operation(*args, **kwargs)
    except ProviderError:
        raise
    except EnvelopeError as exc:
        raise ProviderUpstreamError(provider_name=adapter.key) from exc
    except Exception as exc:
        raise ProviderUnavailable(provider_name=adapter.key) from exc
    if (
        not isinstance(result, AdapterResult)
        or result.provenance.provider != adapter.key
        or result.provenance.category != adapter.category
    ):
        raise ProviderUpstreamError(provider_name=adapter.key)
    if result.usage.latency_ms is None:
        elapsed = round((time.perf_counter() - start) * 1000)
        usage = dataclasses.replace(result.usage, latency_ms=elapsed)
        result = dataclasses.replace(result, usage=usage)
    return result
