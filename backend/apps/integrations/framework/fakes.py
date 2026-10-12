"""In-memory fake adapters, one per category, for tests and local development (issue #74).

No network, no database, no vendor: deterministic output on ``example.com`` placeholders, a fixed
clock, every call recorded in ``adapter.calls`` and an error on demand (``adapter.fail_with =
ProviderRateLimited(30)`` raises on every operation and fails ``test_connection``). Not registered
anywhere by default: tests register them in their own ``AdapterRegistry`` (``register_fakes``).

Import this module explicitly (``apps.integrations.framework.fakes``); the package ``__init__``
does not, so importing the framework loads no fake.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from apps.imports.providers.fake import FakeCompanyProvider
from apps.imports.providers.types import CompanySearchRequest

from .base import Adapter, AdapterCapabilities, ConnectionTestResult, SecretStr
from .categories import (
    DELETE_OBJECT,
    DEPARTMENT_HEADCOUNT,
    ENRICH_COMPANY,
    EXPORT,
    FIND_EMAIL,
    FIND_PEOPLE,
    FUNDING,
    GET_OBJECT,
    HEADCOUNT_GROWTH_3M,
    JOB_POSTINGS,
    PUSH_COMPANY,
    PUSH_CONTACT,
    PUT_OBJECT,
    RUN_STATUS,
    SEARCH,
    SIGNALS,
    STRUCTURED_OUTPUT,
    TRIGGER,
    VERIFY_EMAIL,
    IntegrationCategory,
)
from .envelope import AdapterResult, Usage
from .errors import ProviderBadRequest, ProviderError, ProviderNotFound
from .normalized import (
    CompanyPage,
    CompanyRef,
    DepartmentHeadcount,
    EmailFinding,
    EmailVerification,
    Funding,
    NormalizedCompany,
    NormalizedJobPosting,
    NormalizedPerson,
    PersonRef,
    SignalCandidate,
)
from .operations import (
    MAX_OBJECT_BYTES,
    AutomationAdapter,
    AutomationRun,
    CompanyDataAdapter,
    CRMAdapter,
    CRMCompany,
    CRMContact,
    CRMPushResult,
    EmailEnrichmentAdapter,
    EmailVerificationAdapter,
    ExportReceipt,
    LLMAdapter,
    OutreachAdapter,
    OutreachExport,
    PeopleDataAdapter,
    PromptSpec,
    StorageAdapter,
    StoredObject,
    StructuredSchema,
    clean_payload,
    clean_roles,
    idempotency_key,
    storage_key,
)
from .registry import AdapterRegistry

FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_EMPTY: Mapping[str, Any] = {}


class _FakeAdapter(Adapter):
    """Shared behavior: fixed clock, call log, errors on demand, ``test_connection``."""

    terms: ClassVar[str] = "manual"
    version: ClassVar[str] = "0.1.0"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        fail_with: ProviderError | None = None,
    ) -> None:
        super().__init__(config, credentials, clock=clock or (lambda: FIXED_NOW))
        self.fail_with = fail_with
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _enter(self, operation: str, *args: Any) -> None:
        self.calls.append((operation, args))
        if self.fail_with is not None:
            raise self.fail_with

    def test_connection(self) -> ConnectionTestResult:
        return self.timed_connection_test(lambda: self._enter("test_connection"))

    def _ref(self, name: str) -> str:
        return f"{self.key}:{name}"


# --------------------------------------------------------------------------- company data
class FakeCompanyDataAdapter(_FakeAdapter, CompanyDataAdapter):
    key = "fake-company"
    display_name = "Fake company data"
    capabilities = AdapterCapabilities(
        features=frozenset(
            {
                SEARCH,
                ENRICH_COMPANY,
                JOB_POSTINGS,
                DEPARTMENT_HEADCOUNT,
                HEADCOUNT_GROWTH_3M,
                FUNDING,
                SIGNALS,
            }
        ),
        requires_credentials=False,
        max_page_size=100,
    )

    def search(self, request: CompanySearchRequest) -> AdapterResult[CompanyPage]:
        self._enter("search", request)
        page = FakeCompanyProvider().search(request)
        items = tuple(
            NormalizedCompany(
                name=c.name,
                provider_id=c.provider_id,
                website=c.website,
                domain=c.domain_hint,
                profile_url=c.profile_url,
                country=c.country,
                industry=c.industry,
                employee_count=c.employee_count,
                employee_range=c.employee_range,
                headquarters=c.headquarters,
                description=c.description,
            )
            for c in page.items
        )
        return AdapterResult(
            data=CompanyPage(items, page.next_cursor, page.total_estimate),
            provenance=self.provenance(source_ref=self._ref("search")),
            usage=Usage(credits=Decimal(len(items))),
            warnings=page.warnings,
        )

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        self._enter("enrich_company", company)
        domain = company.domain or ""
        provenance = self.provenance(source_ref=self._ref(f"company:{domain or company.name}"))
        if not domain.endswith("example.com"):
            return AdapterResult(data=None, provenance=provenance)
        data = NormalizedCompany(
            name=domain.split(".")[0].title(),
            domain=domain,
            employee_count=120,
            departments=(DepartmentHeadcount("sales", 12, change_3m=Decimal("8.5")),),
            headcount_change_3m=Decimal("4.2"),
            funding=Funding(
                last_round_type="Series A", last_round_date=date(2025, 6, 1), currency="USD"
            ),
            signals=(
                SignalCandidate(
                    type="sales_hiring",
                    evidence="Opened three sales roles.",
                    source_ref=self._ref("jobs"),
                    event_date=date(2025, 12, 15),
                ),
            ),
        )
        return AdapterResult(
            data=data, provenance=provenance, usage=Usage(credits=Decimal(1)), quality="medium"
        )

    def list_job_postings(
        self, company: CompanyRef
    ) -> AdapterResult[tuple[NormalizedJobPosting, ...]]:
        self._enter("list_job_postings", company)
        postings = (
            NormalizedJobPosting(
                title="Sales Manager", department="sales", posted_date=date(2025, 12, 1)
            ),
        )
        return AdapterResult(
            data=postings, provenance=self.provenance(source_ref=self._ref("jobs"))
        )


# ---------------------------------------------------------------------------- people data
class FakePeopleDataAdapter(_FakeAdapter, PeopleDataAdapter):
    key = "fake-people"
    display_name = "Fake people data"
    capabilities = AdapterCapabilities(
        features=frozenset({FIND_PEOPLE}), requires_credentials=False
    )

    def find_people(
        self, company: CompanyRef, roles: Sequence[str], *, limit: int = 10
    ) -> AdapterResult[tuple[NormalizedPerson, ...]]:
        self._enter("find_people", company, tuple(roles), limit)
        wanted = clean_roles(roles)
        if not 1 <= limit <= 200:
            raise ProviderBadRequest("limit")
        people = tuple(
            NormalizedPerson(
                name=f"Person {i + 1} Example",
                title=role,
                company_domain=company.domain,
                department="sales" if "sales" in role.lower() else None,
            )
            for i, role in enumerate(wanted[:limit])
        )
        return AdapterResult(
            data=people,
            provenance=self.provenance(source_ref=self._ref("people")),
            usage=Usage(credits=Decimal(len(people))),
        )


# ----------------------------------------------------------------------------------- email
class FakeEmailEnrichmentAdapter(_FakeAdapter, EmailEnrichmentAdapter):
    key = "fake-email-finder"
    display_name = "Fake email finder"
    capabilities = AdapterCapabilities(features=frozenset({FIND_EMAIL}), requires_credentials=False)

    def find_email(self, person: PersonRef, domain: str) -> AdapterResult[EmailFinding]:
        self._enter("find_email", person, domain)
        provenance = self.provenance(source_ref=self._ref("email"))
        if "nobody" in person.full_name.lower():
            return AdapterResult(data=EmailFinding(), provenance=provenance)
        local = ".".join(person.full_name.lower().split())
        finding = EmailFinding(
            email=f"{local}@{domain}", status="unknown", confidence=Decimal("0.6")
        )
        return AdapterResult(data=finding, provenance=provenance, usage=Usage(credits=Decimal(1)))


class FakeEmailVerificationAdapter(_FakeAdapter, EmailVerificationAdapter):
    key = "fake-email-verifier"
    display_name = "Fake email verifier"
    capabilities = AdapterCapabilities(
        features=frozenset({VERIFY_EMAIL}), requires_credentials=False
    )

    def verify(self, email: str) -> AdapterResult[EmailVerification]:
        self._enter("verify", email)
        local = email.split("@")[0].lower()
        status = {"invalid": "invalid", "risky": "risky", "catchall": "catch_all"}.get(
            local, "valid"
        )
        data = EmailVerification(
            email=email, status=status, is_disposable=False, is_catch_all=status == "catch_all"
        )
        return AdapterResult(
            data=data,
            provenance=self.provenance(source_ref=self._ref("verify")),
            usage=Usage(credits=Decimal(1)),
        )


# ------------------------------------------------------------------------------------ LLM
class FakeLLMAdapter(_FakeAdapter, LLMAdapter):
    """Returns ``self.output`` (the decoded JSON a model would return) validated by the schema."""

    key = "fake-llm"
    display_name = "Fake LLM"
    capabilities = AdapterCapabilities(
        features=frozenset({STRUCTURED_OUTPUT}), requires_credentials=False
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {"model": {"type": "string", "default": "fake-model", "maxLength": 100}},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, *args: Any, output: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.output: Any = {"ok": True} if output is None else output

    def generate_structured[T](
        self,
        prompt: PromptSpec,
        schema: StructuredSchema[T],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AdapterResult[T]:
        self._enter("generate_structured", prompt, schema.name)
        if not 0.0 <= temperature <= 2.0:
            raise ProviderBadRequest("temperature")
        if not 1 <= max_tokens <= 200_000:
            raise ProviderBadRequest("max_tokens")
        model = str(self.config["model"])
        data = schema.parse(self.output, provider_name=self.key)
        usage = Usage(
            input_tokens=len(prompt.user.split()), output_tokens=len(str(self.output)), model=model
        )
        return AdapterResult(
            data=data,
            provenance=self.provenance(source_ref=self._ref(f"{prompt.name}@{prompt.version}")),
            usage=usage,
        )


# ------------------------------------------------------------------------------------ CRM
class FakeCRMAdapter(_FakeAdapter, CRMAdapter):
    """Idempotent upserts keyed by ``external_key``; requires the credential ``api_key``."""

    key = "fake-crm"
    display_name = "Fake CRM"
    capabilities = AdapterCapabilities(features=frozenset({PUSH_CONTACT, PUSH_COMPANY}))
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {"location_id": {"type": "string", "maxLength": 100}},
        "required": [],
        "additionalProperties": False,
        "x-credentials": {"required": ["api_key"], "optional": []},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.contacts: dict[str, CRMContact] = {}
        self.companies: dict[str, CRMCompany] = {}
        self._ids: dict[str, str] = {}

    def _upsert[R: (CRMContact, CRMCompany)](
        self, store: dict[str, R], record: R
    ) -> AdapterResult[CRMPushResult]:
        key = idempotency_key(record.external_key)
        previous = store.get(key)
        action = (
            "created" if previous is None else ("unchanged" if previous == record else "updated")
        )
        store[key] = record
        remote = self._ids.setdefault(key, f"fake-{len(self._ids) + 1}")
        return AdapterResult(
            data=CRMPushResult(key, remote, action),
            provenance=self.provenance(source_ref=self._ref(remote)),
        )

    def push_contact(self, contact: CRMContact) -> AdapterResult[CRMPushResult]:
        self._enter("push_contact", contact)
        return self._upsert(self.contacts, contact)

    def push_company(self, company: CRMCompany) -> AdapterResult[CRMPushResult]:
        self._enter("push_company", company)
        return self._upsert(self.companies, company)


# -------------------------------------------------------------------------------- outreach
class FakeOutreachAdapter(_FakeAdapter, OutreachAdapter):
    key = "fake-outreach"
    display_name = "Fake outreach export"
    capabilities = AdapterCapabilities(features=frozenset({EXPORT}), requires_credentials=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.exports: list[OutreachExport] = []

    def export_prospects(self, export: OutreachExport) -> AdapterResult[ExportReceipt]:
        self._enter("export_prospects", export)
        self.exports.append(export)
        unique = {r.email for r in export.recipients}
        receipt = ExportReceipt(
            accepted=len(unique),
            rejected=len(export.recipients) - len(unique),
            remote_ref=f"list-{len(self.exports)}",
        )
        return AdapterResult(data=receipt, provenance=self.provenance(source_ref=self._ref("list")))


# --------------------------------------------------------------------------------- storage
class FakeStorageAdapter(_FakeAdapter, StorageAdapter):
    key = "fake-storage"
    display_name = "Fake storage"
    capabilities = AdapterCapabilities(
        features=frozenset({PUT_OBJECT, GET_OBJECT, DELETE_OBJECT}), requires_credentials=False
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.objects: dict[str, tuple[bytes, str | None]] = {}

    def put_object(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> AdapterResult[StoredObject]:
        self._enter("put_object", key)
        name = storage_key(key)
        if not isinstance(data, bytes) or len(data) > MAX_OBJECT_BYTES:
            raise ProviderBadRequest("data")
        self.objects[name] = (data, content_type)
        stored = StoredObject(name, len(data), content_type)
        return AdapterResult(data=stored, provenance=self.provenance(source_ref=self._ref(name)))

    def get_object(self, key: str) -> AdapterResult[bytes]:
        self._enter("get_object", key)
        name = storage_key(key)
        if name not in self.objects:
            raise ProviderNotFound(provider_name=self.key)
        return AdapterResult(
            data=self.objects[name][0], provenance=self.provenance(source_ref=self._ref(name))
        )

    def delete_object(self, key: str) -> AdapterResult[bool]:
        self._enter("delete_object", key)
        name = storage_key(key)
        existed = self.objects.pop(name, None) is not None
        return AdapterResult(data=existed, provenance=self.provenance(source_ref=self._ref(name)))


# ------------------------------------------------------------------------------ automation
class FakeAutomationAdapter(_FakeAdapter, AutomationAdapter):
    key = "fake-automation"
    display_name = "Fake automation"
    capabilities = AdapterCapabilities(
        features=frozenset({TRIGGER, RUN_STATUS}), requires_credentials=False
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.runs: dict[str, str] = {}  # idempotency key -> run id

    def trigger(
        self, workflow: str, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> AdapterResult[AutomationRun]:
        self._enter("trigger", workflow)
        from .operations import idempotency_key as check_key

        key = check_key(idempotency_key)
        clean_payload(payload)
        run_id = self.runs.setdefault(key, f"run-{len(self.runs) + 1}")
        return AdapterResult(
            data=AutomationRun(run_id, "queued"),
            provenance=self.provenance(source_ref=self._ref(run_id)),
        )

    def run_status(self, run_id: str) -> AdapterResult[AutomationRun]:
        self._enter("run_status", run_id)
        if run_id not in self.runs.values():
            raise ProviderNotFound(provider_name=self.key)
        return AdapterResult(
            data=AutomationRun(run_id, "succeeded"),
            provenance=self.provenance(source_ref=self._ref(run_id)),
        )


FAKE_ADAPTERS: dict[IntegrationCategory, type[Adapter]] = {
    IntegrationCategory.COMPANY_DATA: FakeCompanyDataAdapter,
    IntegrationCategory.PEOPLE_DATA: FakePeopleDataAdapter,
    IntegrationCategory.EMAIL_ENRICHMENT: FakeEmailEnrichmentAdapter,
    IntegrationCategory.EMAIL_VERIFICATION: FakeEmailVerificationAdapter,
    IntegrationCategory.LLM: FakeLLMAdapter,
    IntegrationCategory.CRM: FakeCRMAdapter,
    IntegrationCategory.OUTREACH: FakeOutreachAdapter,
    IntegrationCategory.STORAGE: FakeStorageAdapter,
    IntegrationCategory.AUTOMATION: FakeAutomationAdapter,
}


def register_fakes(registry: AdapterRegistry) -> None:
    """Register the nine fakes in ``registry`` (tests and local dev; never done on import)."""
    for cls in FAKE_ADAPTERS.values():
        registry.register(cls, replace=True)
