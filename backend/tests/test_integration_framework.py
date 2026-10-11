"""Integration framework tests (issues #74 and #75, ADR 0013): contract suites for the fakes,
registry, envelope, normalized types, errors, import boundaries and #66 compatibility."""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest

from apps.imports.providers import (
    FakeCompanyProvider,
    execute_search,
)
from apps.imports.providers import ProviderError as ImportsProviderError
from apps.imports.providers import registry as imports_registry
from apps.imports.providers.base import ALLOWED_TERMS as IMPORTS_ALLOWED_TERMS
from apps.imports.providers.types import CompanySearchRequest
from apps.integrations.framework import (
    ALLOWED_TERMS,
    Adapter,
    AdapterCapabilities,
    AdapterRegistry,
    AdapterResult,
    CompanyDataAdapter,
    CompanyRef,
    ConnectionSpec,
    ConnectionTestResult,
    DepartmentHeadcount,
    EmailVerification,
    EnvelopeError,
    Funding,
    IntegrationCategory,
    NormalizedCompany,
    NormalizedPerson,
    PeopleDataAdapter,
    Provenance,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderConflict,
    ProviderError,
    ProviderInvalidOutput,
    ProviderNotConfigured,
    ProviderNotFound,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderRegistryError,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUpstreamError,
    SecretStr,
    SignalCandidate,
    StructuredSchema,
    Usage,
    default_registry,
    execute,
    map_http_status,
    parse_retry_after,
    supports,
)
from apps.integrations.framework import errors as framework_errors
from apps.integrations.framework.bridge import as_company_search_provider
from apps.integrations.framework.categories import CATEGORY_FEATURES, check_terms
from apps.integrations.framework.fakes import (
    FAKE_ADAPTERS,
    FIXED_NOW,
    FakeAutomationAdapter,
    FakeCompanyDataAdapter,
    FakeCRMAdapter,
    FakeEmailEnrichmentAdapter,
    FakeEmailVerificationAdapter,
    FakeLLMAdapter,
    FakeOutreachAdapter,
    FakePeopleDataAdapter,
    FakeStorageAdapter,
    register_fakes,
)
from apps.integrations.framework.normalized import SIGNAL_TYPES
from apps.integrations.framework.operations import OutreachAdapter, PromptSpec
from tests.adapter_contract import (
    AutomationContract,
    CompanyDataContract,
    CRMContract,
    EmailEnrichmentContract,
    EmailVerificationContract,
    LLMContract,
    OutreachContract,
    PeopleDataContract,
    StorageContract,
)

BACKEND = Path(__file__).resolve().parent.parent
FRAMEWORK = BACKEND / "apps" / "integrations" / "framework"
SECRET = "Sk/Live/Abc123"  # pragma: allowlist secret


# ============================================================ contract suites over the fakes
class TestFakeCompany(CompanyDataContract):
    adapter_cls = FakeCompanyDataAdapter


class TestFakePeople(PeopleDataContract):
    adapter_cls = FakePeopleDataAdapter


class TestFakeEmailFinder(EmailEnrichmentContract):
    adapter_cls = FakeEmailEnrichmentAdapter


class TestFakeEmailVerifier(EmailVerificationContract):
    adapter_cls = FakeEmailVerificationAdapter


class TestFakeLLM(LLMContract):
    adapter_cls = FakeLLMAdapter


class TestFakeCRM(CRMContract):
    adapter_cls = FakeCRMAdapter


class TestFakeOutreach(OutreachContract):
    adapter_cls = FakeOutreachAdapter


class TestFakeStorage(StorageContract):
    adapter_cls = FakeStorageAdapter


class TestFakeAutomation(AutomationContract):
    adapter_cls = FakeAutomationAdapter


def test_every_category_has_a_fake_and_all_nine_categories_exist() -> None:
    assert {c.value for c in IntegrationCategory} == {
        "llm",
        "company_data",
        "people_data",
        "email_enrichment",
        "email_verification",
        "crm",
        "outreach",
        "storage",
        "automation",
    }
    assert set(FAKE_ADAPTERS) == set(IntegrationCategory)
    assert set(CATEGORY_FEATURES) == set(IntegrationCategory)


# ====================================================================================== registry
def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    register_fakes(registry)
    return registry


def test_importing_registers_nothing_and_loads_no_fake() -> None:
    code = (
        "import sys, django, os\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.test')\n"
        "import apps.integrations.framework as f\n"
        "import apps.imports.providers as p\n"
        "assert len(f.default_registry) == 0 and len(p.default_registry) == 0\n"
        "assert 'apps.integrations.framework.fakes' not in sys.modules\n"
        "assert 'apps.integrations.framework.bridge' not in sys.modules\n"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


def test_default_registry_starts_empty_here_too() -> None:
    assert len(default_registry) == 0


def test_register_list_and_lookup_by_category_and_key() -> None:
    registry = _registry()
    assert len(registry) == 9
    keys = [(c.category, c.key) for c in registry.list()]
    assert keys == sorted(keys)
    assert registry.list(IntegrationCategory.CRM) == [FakeCRMAdapter]
    assert (IntegrationCategory.CRM, "fake-crm") in registry
    assert registry.get_class("crm", "fake-crm") is FakeCRMAdapter


def test_unknown_key_and_category_give_a_clear_error() -> None:
    registry = _registry()
    for category, key in (("crm", "nope"), ("bogus", "x")):
        with pytest.raises(ProviderNotFound) as info:
            registry.get_class(category, key)
        assert info.value.code == "provider_not_found"
        assert SECRET not in str(info.value)


def test_duplicate_registration_is_refused_unless_replace() -> None:
    registry = _registry()
    with pytest.raises(ProviderRegistryError) as info:
        registry.register(FakeCRMAdapter)
    assert info.value.code == "duplicate_adapter"
    registry.register(FakeCRMAdapter, replace=True)
    registry.unregister("crm", "fake-crm")
    assert (IntegrationCategory.CRM, "fake-crm") not in registry
    registry.clear()
    assert len(registry) == 0


def test_resolve_builds_adapter_from_connection_spec() -> None:
    registry = _registry()
    spec = ConnectionSpec("crm", "fake-crm", {"location_id": "L1"}, {"api_key": "YOUR_API_KEY"})
    adapter = registry.resolve(spec)
    assert isinstance(adapter, FakeCRMAdapter)
    assert adapter.config["location_id"] == "L1"
    assert adapter.credential("api_key").reveal() == "YOUR_API_KEY"
    with pytest.raises(ProviderNotFound):
        registry.resolve(ConnectionSpec("crm", "missing"))


def test_resolve_reports_missing_credentials_by_name_only() -> None:
    registry = _registry()
    with pytest.raises(ProviderNotConfigured) as info:
        registry.resolve(ConnectionSpec("crm", "fake-crm", {"location_id": 5}, {}))
    assert info.value.fields == ("location_id", "api_key")
    assert info.value.as_dict()["fields"] == ["location_id", "api_key"]
    with pytest.raises(ProviderNotConfigured):
        ConnectionSpec("not-a-category", "x")


def test_describe_is_json_safe_and_secret_free() -> None:
    described = _registry().describe()
    text = json.dumps(described)
    assert "fake-crm" in text
    crm = next(d for d in described if d["key"] == "fake-crm")
    assert crm["features"] == ["push_company", "push_contact"]
    assert crm["config_schema"]["x-credentials"]["required"] == ["api_key"]


def test_capability_negotiation() -> None:
    registry = _registry()
    assert registry.supporting("company_data", "funding") == [FakeCompanyDataAdapter]
    assert registry.supporting("company_data", "headcount_growth_12m") == []
    assert registry.supports("crm", "fake-crm", "push_contact") is True
    assert supports(FakeCRMAdapter, "push_company")
    assert supports(FakeCompanyDataAdapter.capabilities, "search")
    assert supports(FakeLLMAdapter, "structured_output")
    with pytest.raises(ValueError, match="not a feature"):
        supports(FakeCRMAdapter, "search")


class _Base(OutreachAdapter):
    key = "good-outreach"
    display_name = "Good"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(features=frozenset({"export"}))

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult.success()


def _variant(**attrs: Any) -> type[Adapter]:
    return type("Variant", (_Base,), attrs)


@pytest.mark.parametrize(
    ("attrs", "code"),
    [
        ({"key": "Bad Key"}, "invalid_key"),
        ({"key": "a:b"}, "invalid_key"),
        ({"display_name": ""}, "invalid_display_name"),
        ({"terms": "scraping"}, "terms_forbidden"),
        ({"terms": "web_crawler"}, "terms_forbidden"),
        ({"terms": "mystery"}, "terms_unknown"),
        ({"terms": ""}, "terms_missing"),
        (
            {"capabilities": AdapterCapabilities(features=frozenset({"send"}))},
            "invalid_capabilities",
        ),
        ({"capabilities": "nope"}, "invalid_capabilities"),
        ({"config_schema": {"type": "array"}}, "invalid_config_schema"),
        (
            {"config_schema": {"type": "object", "properties": {"api_key": {"type": "string"}}}},
            "invalid_config_schema",
        ),
        (
            {"config_schema": {"type": "object", "x-credentials": {"required": ["Bad Name"]}}},
            "invalid_config_schema",
        ),
        ({"config_schema": {"type": "object", "required": ["ghost"]}}, "invalid_config_schema"),
    ],
)
def test_registration_refusals(attrs: dict[str, Any], code: str) -> None:
    with pytest.raises(ProviderRegistryError) as info:
        AdapterRegistry().register(_variant(**attrs))
    assert info.value.code == code


def test_registration_refuses_non_adapters_abstract_and_wrong_category() -> None:
    registry = AdapterRegistry()
    with pytest.raises(ProviderRegistryError) as info:
        registry.register(object)  # type: ignore[arg-type]
    assert info.value.code == "invalid_adapter"

    class Abstract(OutreachAdapter):
        key = "abstract"
        display_name = "x"
        terms = "manual"
        capabilities = AdapterCapabilities()

    with pytest.raises(ProviderRegistryError) as info:
        registry.register(Abstract)  # type: ignore[type-abstract]
    assert info.value.code == "invalid_adapter"

    class WrongCategory(_Base):
        category = IntegrationCategory.CRM
        capabilities = AdapterCapabilities()

    with pytest.raises(ProviderRegistryError) as info:
        registry.register(WrongCategory)
    assert info.value.code == "category_mismatch"

    class NoCategory(_Base):
        category = "nonsense"  # type: ignore[assignment]

    with pytest.raises(ProviderRegistryError) as info:
        registry.register(NoCategory)
    assert info.value.code == "invalid_category"


def test_terms_checker_is_shared_with_the_import_contract() -> None:
    assert IMPORTS_ALLOWED_TERMS is ALLOWED_TERMS
    assert imports_registry.check_terms is check_terms
    for ok in ("licensed_api", "public_data", "manual", " Manual "):
        check_terms(ok)


# ================================================================ config schema and secrets
class _Configured(PeopleDataAdapter):
    key = "configured"
    display_name = "Configured"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(features=frozenset({"find_people"}))
    config_schema: ClassVar[dict[str, Any]] = {  # type: ignore[misc]
        "type": "object",
        "properties": {
            "region": {"type": "string", "enum": ["eu", "us"], "default": "eu"},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
            "label": {"type": "string", "maxLength": 5},
            "dry_run": {"type": "boolean"},
        },
        "required": ["page_size"],
        "additionalProperties": False,
        "x-credentials": {"required": ["api_key"], "optional": ["org_id"]},
    }

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult.success()


@pytest.mark.parametrize(
    ("config", "creds", "issues"),
    [
        ({"page_size": 10}, {"api_key": "k"}, set()),
        ({}, {"api_key": "k"}, {("page_size", "required")}),
        ({"page_size": 10}, {}, {("api_key", "missing_credential")}),
        ({"page_size": 10}, {"api_key": "  "}, {("api_key", "missing_credential")}),
        ({"page_size": "10"}, {"api_key": "k"}, {("page_size", "type")}),
        ({"page_size": True}, {"api_key": "k"}, {("page_size", "type")}),
        ({"page_size": 0}, {"api_key": "k"}, {("page_size", "range")}),
        ({"page_size": 99}, {"api_key": "k"}, {("page_size", "range")}),
        ({"page_size": 1, "region": "asia"}, {"api_key": "k"}, {("region", "enum")}),
        ({"page_size": 1, "label": "toolong"}, {"api_key": "k"}, {("label", "length")}),
        ({"page_size": 1, "dry_run": 1}, {"api_key": "k"}, {("dry_run", "type")}),
        ({"page_size": 1, "x": 1}, {"api_key": "k"}, {("x", "unknown_field")}),
        ({"page_size": 1}, {"api_key": "k", "zzz": "v"}, {("zzz", "unknown_credential")}),
        ({"page_size": 1}, {"api_key": "k", "org_id": "o"}, set()),
    ],
)
def test_validate_config_reports_names_and_codes(
    config: dict[str, Any], creds: dict[str, str], issues: set[tuple[str, str]]
) -> None:
    result = _Configured.validate_config(config, creds)
    assert {(i.field, i.code) for i in result.issues} == issues
    assert result.ok is (not issues)
    assert json.dumps(result.as_dict())


def test_config_defaults_apply_and_none_means_unset() -> None:
    adapter = _Configured({"page_size": 3, "region": None}, {"api_key": "k"})
    assert adapter.config == {"region": "eu", "page_size": 3}
    with pytest.raises(TypeError):
        adapter.config["x"] = 1  # type: ignore[index]
    with pytest.raises(ProviderNotConfigured):
        adapter.credential("nope")


def test_secret_str_never_prints_or_pickles() -> None:
    secret = SecretStr(SECRET)
    for shown in (str(secret), repr(secret), f"{secret}", f"{secret!r}", "%s" % secret):  # noqa: UP031
        assert SECRET not in shown
    assert secret.reveal() == SECRET
    assert secret == SecretStr(SECRET)
    assert bool(SecretStr(" ")) is False
    assert hash(secret) == hash(SecretStr(SECRET))
    with pytest.raises(TypeError):
        SecretStr(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        json.dumps({"k": secret})


def test_connection_spec_wraps_credentials_and_hides_them() -> None:
    spec = ConnectionSpec(IntegrationCategory.CRM, "x", {"a": 1}, {"api_key": SECRET})
    assert isinstance(spec.credentials["api_key"], SecretStr)
    assert SECRET not in repr(spec)
    with pytest.raises(TypeError):
        spec.config["b"] = 2  # type: ignore[index]


def test_connection_test_result_failure_uses_the_safe_message() -> None:
    err = ProviderAuthError(provider_name="acme")
    result = ConnectionTestResult.failure(err, 12)
    assert (result.ok, result.error_code, result.latency_ms) == (False, "provider_auth", 12)
    assert result.message == ProviderAuthError.message
    leaky = ConnectionTestResult(False, f"failed with token={SECRET}")
    assert SECRET not in leaky.message
    with pytest.raises(ValueError, match="timezone"):
        ConnectionTestResult(True, "ok", checked_at=datetime(2026, 1, 1))


def test_timed_connection_test_maps_unexpected_exceptions() -> None:
    adapter = FakeCRMAdapter({}, {"api_key": "k"})

    def boom() -> None:
        raise RuntimeError(SECRET)

    result = adapter.timed_connection_test(boom)
    assert result.ok is False
    assert SECRET not in repr(result)


# ====================================================================================== envelope
NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)


def _prov(**kw: Any) -> Provenance:
    base: dict[str, Any] = {
        "provider": "acme",
        "category": IntegrationCategory.COMPANY_DATA,
        "retrieved_at": NOW,
        "terms": "licensed_api",
        "source_ref": "acme:123",
    }
    return Provenance(**{**base, **kw})


def test_provenance_requires_tz_aware_retrieved_at() -> None:
    with pytest.raises(EnvelopeError, match="timezone"):
        _prov(retrieved_at=datetime(2026, 5, 1))
    with pytest.raises(EnvelopeError):
        _prov(retrieved_at="2026-05-01")
    assert _prov(retrieved_at=datetime(2026, 5, 1, tzinfo=timezone(timedelta(hours=3))))


@pytest.mark.parametrize(
    "kw",
    [
        {"provider": "Bad Name"},
        {"provider": ""},
        {"category": "nope"},
        {"terms": "scraping"},
        {"terms": "weird"},
        {"source_url": "ftp://x.example.com/a"},
        {"source_url": "https://user:pw@example.com/a"},  # pragma: allowlist secret
        {"source_url": "not a url"},
        {"evidence_date": "2026-01-01"},
    ],
)
def test_provenance_rejects_bad_fields(kw: dict[str, Any]) -> None:
    with pytest.raises(EnvelopeError):
        _prov(**kw)


def test_provenance_fields_and_scrubbing() -> None:
    prov = _prov(
        source_url="https://example.com/jobs",
        evidence_date=datetime(2026, 4, 1, 8, tzinfo=UTC),
        request_id="req-1",
        cache_hit=True,
        raw_ref=f"s3://bucket/raw?token={SECRET}",
    )
    assert prov.evidence_date == date(2026, 4, 1)
    assert prov.cache_hit is True
    assert SECRET not in (prov.raw_ref or "")
    assert SECRET not in json.dumps(prov.as_dict())
    assert prov.as_dict()["category"] == "company_data"
    assert _prov(cache_hit="yes").cache_hit is False  # type: ignore[arg-type]
    assert len(_prov(raw_ref="x" * 5000).raw_ref or "") <= 200


def test_evidence_requires_a_source_in_provenance() -> None:
    company = NormalizedCompany(name="Acme")
    with pytest.raises(EnvelopeError, match="source"):
        AdapterResult(data=company, provenance=_prov(source_ref=None))
    with pytest.raises(EnvelopeError):
        AdapterResult(data=(company,), provenance=_prov(source_ref=None))
    # a source URL is enough, and non-evidence data needs no source
    assert AdapterResult(
        data=company, provenance=_prov(source_ref=None, source_url="https://e.com/")
    )
    assert AdapterResult(data=True, provenance=_prov(source_ref=None))
    assert AdapterResult(data=None, provenance=_prov(source_ref=None)).has_data is False


def test_result_needs_real_provenance_and_usage() -> None:
    with pytest.raises(EnvelopeError):
        AdapterResult(data=None, provenance=None)  # type: ignore[arg-type]
    with pytest.raises(EnvelopeError):
        AdapterResult(data=None, provenance=_prov(), usage=3)  # type: ignore[arg-type]
    with pytest.raises(EnvelopeError):
        AdapterResult(data=None, provenance=_prov(), warnings="oops")  # type: ignore[arg-type]


def test_warnings_are_scrubbed_and_capped_and_quality_checked() -> None:
    result: AdapterResult[Any] = AdapterResult(
        data=None,
        provenance=_prov(),
        warnings=(f"skipped record token={SECRET}", "", "x" * 500, *["w"] * 40),
        quality="high",
    )
    assert SECRET not in " ".join(result.warnings)
    assert len(result.warnings) == 20
    assert all(len(w) <= 200 for w in result.warnings)
    assert result.quality == "high"
    assert AdapterResult(data=None, provenance=_prov(), quality="great").quality is None


def test_usage_keeps_decimals_and_drops_garbage() -> None:
    usage = Usage(
        credits=Decimal("1.5"), cost="0.0021", currency="usd", input_tokens=10, output_tokens=5
    )
    assert usage.cost == Decimal("0.0021")
    assert usage.currency == "USD"
    assert usage.total_tokens == 15
    bad = Usage(credits=-1, cost="NaN", currency="dollars", input_tokens=-3, latency_ms=True)
    assert (bad.credits, bad.cost, bad.currency, bad.input_tokens, bad.latency_ms) == (None,) * 5
    assert bad.total_tokens is None
    assert Usage(credits=float("inf")).credits is None


def test_log_summary_has_no_data() -> None:
    result = AdapterResult(
        data=NormalizedCompany(name="Secretive Corp"),
        provenance=_prov(),
        usage=Usage(credits=Decimal(2), model="m"),
    )
    summary = result.log_summary()
    assert "Secretive" not in json.dumps(summary)
    assert summary["credits"] == "2"
    assert summary["has_data"] is True


# ============================================================================ normalized data
def test_unknown_stays_none_not_defaulted() -> None:
    company = NormalizedCompany(name="Acme")
    for name in (
        "website",
        "domain",
        "country",
        "industry",
        "employee_count",
        "headcount_change_3m",
        "funding",
        "provider_id",
    ):
        assert getattr(company, name) is None
    assert company.departments == ()
    assert company.signals == ()
    person = NormalizedPerson(name="Alex")
    assert (person.email, person.title, person.department) == (None, None, None)
    assert person.email_status == "unknown"


def test_company_cleans_bad_values_to_none() -> None:
    company = NormalizedCompany(
        name="  Acme  ",
        website="javascript:alert(1)",
        domain="WWW.Acme.COM",
        profile_url="https://www.linkedin.com/company/acme",
        country="zz",
        employee_count=-4,
        headcount_change_3m="not a number",
        headcount_change_6m=Decimal("-250"),
        headcount_change_12m="12.5",
        confidence=7,
    )
    assert company.name == "Acme"
    assert company.website is None
    assert company.domain == "acme.com"
    assert company.profile_url == "https://www.linkedin.com/company/acme"
    assert company.country is None
    assert company.employee_count is None
    assert company.headcount_change_3m is None
    assert company.headcount_change_6m is None
    assert company.headcount_change_12m == Decimal("12.5")
    assert company.confidence is None
    assert NormalizedCompany(name="A", country="sa").country == "SA"


def test_percent_changes_are_decimals_not_floats() -> None:
    dept = DepartmentHeadcount("sales", 10, change_3m=0.1, change_6m=Decimal("5"), change_12m="7")
    assert dept.change_3m == Decimal("0.1")
    assert all(isinstance(v, Decimal) for v in (dept.change_3m, dept.change_6m, dept.change_12m))


@pytest.mark.parametrize(
    "build",
    [
        lambda: NormalizedCompany(name="  "),
        lambda: NormalizedCompany(name=None),  # type: ignore[arg-type]
        lambda: NormalizedPerson(name=""),
        lambda: DepartmentHeadcount("legal"),
        lambda: NormalizedCompany(
            name="A", departments=(DepartmentHeadcount("sales"), DepartmentHeadcount("sales"))
        ),
        lambda: NormalizedCompany(name="A", departments=("sales",)),  # type: ignore[arg-type]
        lambda: NormalizedCompany(name="A", signals=("x",)),  # type: ignore[arg-type]
        lambda: SignalCandidate("made_up", "ev", source_ref="r"),
        lambda: SignalCandidate("funding", "  ", source_ref="r"),
        lambda: SignalCandidate("funding", "Raised a round."),
        lambda: SignalCandidate("funding", "Raised.", source_url="ftp://x"),
        lambda: EmailVerification(email="not-an-email"),
        lambda: CompanyRef(),
    ],
)
def test_required_parts_are_enforced(build: Any) -> None:
    with pytest.raises(EnvelopeError):
        build()


def test_signal_candidate_and_funding() -> None:
    sig = SignalCandidate(
        "funding", f"Raised $5m token={SECRET}", source_url="https://news.example.com/a",
        event_date=datetime(2026, 3, 1, 10, tzinfo=UTC), confidence="0.8",
    )  # fmt: skip
    assert sig.event_date == date(2026, 3, 1)
    assert SECRET not in sig.evidence
    assert sig.confidence == Decimal("0.8")
    fund = Funding(total_raised="5000000", currency="usd", last_round_date=date(2026, 3, 1))
    assert fund.total_raised == Decimal(5000000)
    assert fund.currency == "USD"
    assert Funding(total_raised=-1, currency="x").total_raised is None


def test_signal_types_match_the_research_model() -> None:
    from apps.research.models import SignalType

    assert {value for value, _ in SignalType.choices} == SIGNAL_TYPES


def test_size_caps() -> None:
    long = "x" * 10_000
    company = NormalizedCompany(
        name=long,
        description=long,
        industry=long,
        headquarters=long,
        extra={f"k{i}": i for i in range(100)},
    )
    assert len(company.name) == 300
    assert len(company.description or "") == 2000
    assert len(company.industry or "") == 200
    assert len(company.extra) == 20
    sigs = tuple(SignalCandidate("other", "e", source_ref="r") for _ in range(51))
    with pytest.raises(EnvelopeError):
        NormalizedCompany(name="A", signals=sigs)
    assert len(SignalCandidate("other", long, source_ref="r").evidence) == 1000


def test_extra_is_scalar_and_scrubbed() -> None:
    company = NormalizedCompany(
        name="A",
        extra={
            "api_key": SECRET,
            "note": f"Bearer {SECRET}12345",
            "nested": {"a": 1},
            "n": 3,
            "f": 1.5,
        },
    )
    shown = json.dumps(company.extra)
    assert SECRET not in shown
    assert "nested" not in company.extra
    assert company.extra["n"] == 3


def test_person_email_and_status() -> None:
    person = NormalizedPerson(
        name="Alex", email="Alex@ACME.com", email_status="great", department="sales",
        seniority="vp", company_domain="Acme.com",
    )  # fmt: skip
    assert person.email == "Alex@acme.com"
    assert person.email_status == "unknown"
    assert (person.department, person.seniority, person.company_domain) == (
        "sales",
        "vp",
        "acme.com",
    )
    assert (
        NormalizedPerson(name="A", email="bad", department="legal", seniority="boss").email is None
    )
    verdict = EmailVerification("a@acme.com", "valid", is_disposable=False, mx_found="yes")  # type: ignore[arg-type]
    assert verdict.deliverable is True
    assert verdict.mx_found is None
    assert EmailVerification("a@acme.com", "invalid").deliverable is False
    assert EmailVerification("a@acme.com", "risky").deliverable is None


def test_normalized_company_converts_to_the_import_contract() -> None:
    company = NormalizedCompany(
        name="Acme", provider_id="42", domain="acme.com", country="SA", employee_count=50,
        confidence="0.5",
    )  # fmt: skip
    legacy = company.to_provider_company()
    assert (legacy.name, legacy.domain_hint, legacy.country, legacy.provider_id) == (
        "Acme", "acme.com", "SA", "42",
    )  # fmt: skip
    assert legacy.website == ""
    assert legacy.confidence == 0.5
    assert company.department("sales") is None


# ======================================================================================= errors
ERROR_TABLE = [
    (ProviderAuthError, "provider_auth", False),
    (ProviderRateLimited, "provider_rate_limited", True),
    (ProviderUnavailable, "provider_unavailable", True),
    (ProviderTimeout, "provider_timeout", True),
    (ProviderBadRequest, "provider_bad_request", False),
    (ProviderQuotaExceeded, "provider_quota_exceeded", False),
    (ProviderNotConfigured, "provider_not_configured", False),
    (ProviderNotFound, "provider_not_found", False),
    (ProviderConflict, "provider_conflict", False),
    (ProviderUpstreamError, "provider_upstream_error", False),
    (ProviderInvalidOutput, "provider_invalid_output", False),
]


@pytest.mark.parametrize(("cls", "code", "retryable"), ERROR_TABLE)
def test_error_table(cls: type[ProviderError], code: str, retryable: bool) -> None:
    err = cls(provider_name="acme", upstream_status=500)
    assert (err.code, err.retryable) == (code, retryable)
    data = err.as_dict()
    assert data["code"] == code
    assert data["retryable"] is retryable
    assert set(data) >= {"code", "message", "provider", "retryable", "retry_after"}
    assert json.dumps(data)
    assert str(err) == cls.message


def test_error_codes_are_unique_and_all_listed() -> None:
    assert len({c for _, c, _ in ERROR_TABLE}) == len(ERROR_TABLE)
    assert {c for c, _, _ in ERROR_TABLE} == set(framework_errors.ALL_ERROR_CLASSES)


def test_timeout_is_an_unavailable_and_invalid_output_an_upstream_error() -> None:
    assert issubclass(ProviderTimeout, ProviderUnavailable)
    assert issubclass(ProviderInvalidOutput, ProviderUpstreamError)


def test_retry_after_rules() -> None:
    assert ProviderRateLimited(12).retry_after == 12.0
    assert ProviderRateLimited(-1).retry_after == 0.0
    assert ProviderRateLimited(10**9).retry_after == 86400.0
    assert ProviderRateLimited(float("nan")).retry_after is None
    assert ProviderRateLimited("soon").retry_after is None  # type: ignore[arg-type]
    assert ProviderUnavailable(retry_after=30).retry_after == 30.0
    assert ProviderAuthError(retry_after=30).retry_after is None  # not retryable: meaningless
    assert ProviderRateLimited(7).as_dict()["retry_after"] == 7.0


def test_errors_never_carry_upstream_text() -> None:
    body = '{"error":"bad key Sk/Live/Abc123"}'  # pragma: allowlist secret
    for cls, _, _ in ERROR_TABLE:
        err = cls(provider_name=SECRET, upstream_status=body)
        text = str(err) + repr(err.as_dict())
        assert SECRET not in text
        assert body not in text
        assert err.upstream_status is None
    nc = ProviderNotConfigured(["api_key", f"x {SECRET}", "location_id"])
    assert nc.fields == ("api_key", "location_id")
    assert SECRET not in repr(nc.as_dict())


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (400, ProviderBadRequest),
        (401, ProviderAuthError),
        (402, ProviderQuotaExceeded),
        (403, ProviderAuthError),
        (404, ProviderNotFound),
        (405, ProviderBadRequest),
        (408, ProviderTimeout),
        (409, ProviderConflict),
        (410, ProviderNotFound),
        (413, ProviderBadRequest),
        (415, ProviderBadRequest),
        (418, ProviderUpstreamError),
        (422, ProviderBadRequest),
        (429, ProviderRateLimited),
        (500, ProviderUnavailable),
        (502, ProviderUnavailable),
        (503, ProviderUnavailable),
        (504, ProviderTimeout),
        (599, ProviderUnavailable),
        (301, ProviderUpstreamError),
        (200, ProviderUpstreamError),
        (100, ProviderUpstreamError),
    ],
)
def test_map_http_status_table(status: int, cls: type[ProviderError]) -> None:
    err = map_http_status(status, provider_name="acme")
    assert type(err) is cls
    assert err.upstream_status == status
    assert err.provider_name == "acme"


def test_map_http_status_retry_after_and_garbage() -> None:
    assert map_http_status(429, retry_after="30").retry_after == 30.0  # type: ignore[attr-defined]
    assert map_http_status(503, retry_after=5).retry_after == 5.0
    assert map_http_status(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT").retry_after is None
    assert map_http_status(401, retry_after=5).retry_after is None
    assert type(map_http_status("500")) is ProviderUpstreamError  # type: ignore[arg-type]
    assert [parse_retry_after(v) for v in ("7", " 2.5 ", "-1", "abc", None, True, 3)] == [
        7.0, 2.5, None, None, None, None, 3.0,
    ]  # fmt: skip


def test_bad_request_field_is_name_only() -> None:
    assert "Field: query." in str(ProviderBadRequest("query"))
    assert ProviderBadRequest(f"query {SECRET}").field == ""


# ===================================================================================== execute
class _Probe(CompanyDataAdapter):
    key = "probe"
    display_name = "Probe"
    terms = "manual"
    capabilities = AdapterCapabilities(
        features=frozenset({"enrich_company"}), requires_credentials=False
    )
    behavior: Any = None

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult.success()

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        return self.behavior(self)  # type: ignore[no-any-return]


def _run(behavior: Any) -> AdapterResult[NormalizedCompany]:
    adapter = _Probe()
    adapter.behavior = behavior
    return execute(adapter, adapter.enrich_company, CompanyRef(name="Acme"))


def test_execute_wraps_unexpected_errors_without_leaking() -> None:
    def explode(_: Any) -> Any:
        raise RuntimeError(SECRET)

    with pytest.raises(ProviderUnavailable) as info:
        _run(explode)
    assert SECRET not in str(info.value) + repr(info.value.as_dict())
    assert isinstance(info.value.__cause__, RuntimeError)


def test_execute_turns_envelope_bugs_into_upstream_errors() -> None:
    def no_provenance(adapter: Adapter) -> Any:
        return AdapterResult(NormalizedCompany(name="A"), adapter.provenance())  # no source

    with pytest.raises(ProviderUpstreamError):
        _run(no_provenance)


def test_execute_checks_the_stamp_and_fills_latency() -> None:
    with pytest.raises(ProviderUpstreamError):
        _run(lambda a: "not a result")
    with pytest.raises(ProviderUpstreamError):
        _run(lambda a: AdapterResult(None, _prov(provider="other")))
    with pytest.raises(ProviderUpstreamError):
        _run(lambda a: AdapterResult(None, _prov(provider="probe", category="crm")))
    ok = _run(lambda a: AdapterResult(None, _prov(provider="probe")))
    assert ok.usage.latency_ms is not None
    kept = _run(lambda a: AdapterResult(None, _prov(provider="probe"), Usage(latency_ms=7)))
    assert kept.usage.latency_ms == 7


def test_execute_refuses_undeclared_feature() -> None:
    adapter = _Probe()
    with pytest.raises(ProviderBadRequest):
        execute(adapter, adapter.search, CompanySearchRequest())


def test_default_operations_are_unsupported() -> None:
    with pytest.raises(ProviderBadRequest):
        _Probe().list_job_postings(CompanyRef(name="A"))


# =================================================================================== LLM schema
def test_structured_schema_failure_is_a_typed_error_without_model_text() -> None:
    def validate(raw: Any) -> int:
        raise ValueError(f"model said {SECRET}")

    schema = StructuredSchema("n", {"type": "integer"}, validate)
    with pytest.raises(ProviderInvalidOutput) as info:
        schema.parse("x", provider_name="fake-llm")
    assert SECRET not in str(info.value)
    assert info.value.__cause__ is None
    adapter = FakeLLMAdapter(output="garbage")
    with pytest.raises(ProviderInvalidOutput):
        execute(adapter, adapter.generate_structured, PromptSpec("p", "1", "hi"), schema)


def test_fake_llm_reports_model_and_validates_prompt() -> None:
    adapter = FakeLLMAdapter({"model": "m-1"}, output={"score": 3})
    schema = StructuredSchema("s", {"type": "object"}, lambda raw: int(raw["score"]))
    result = execute(adapter, adapter.generate_structured, PromptSpec("icp", "2", "a b c"), schema)
    assert result.data == 3
    assert (result.usage.model, result.usage.input_tokens) == ("m-1", 3)
    assert result.provenance.source_ref == "fake-llm:icp@2"
    with pytest.raises(ProviderBadRequest):
        PromptSpec("", "1", "x")
    with pytest.raises(ProviderBadRequest):
        execute(
            adapter, adapter.generate_structured, PromptSpec("p", "1", "x"), schema, max_tokens=0
        )


# ============================================================================ fakes in detail
def test_fakes_are_deterministic_and_use_a_fixed_clock() -> None:
    adapter = FakeCompanyDataAdapter()
    first = execute(adapter, adapter.search, CompanySearchRequest(page_size=3))
    second = execute(adapter, adapter.search, CompanySearchRequest(page_size=3))
    assert first.data == second.data
    assert first.provenance.retrieved_at == FIXED_NOW
    assert first.data is not None
    assert first.data.next_cursor is not None
    assert first.usage.credits == Decimal(3)


def test_fake_enrichment_fills_only_declared_fields() -> None:
    adapter = FakeCompanyDataAdapter()
    found = execute(adapter, adapter.enrich_company, CompanyRef(domain="acme.example.com"))
    assert found.data is not None
    assert found.data.department("sales") is not None
    assert found.data.headcount_change_12m is None  # not declared, so never filled
    assert found.data.signals[0].type == "sales_hiring"
    missing = execute(adapter, adapter.enrich_company, CompanyRef(domain="x.test.org"))
    assert missing.data is None
    assert missing.provenance.has_source


def test_fake_email_finder_returns_none_when_nothing_found() -> None:
    from apps.integrations.framework import PersonRef

    adapter = FakeEmailEnrichmentAdapter()
    result = execute(adapter, adapter.find_email, PersonRef("Mr Nobody"), "acme.example.com")
    assert result.data is not None
    assert result.data.email is None


def test_fake_crm_actions_and_outreach_dedupe() -> None:
    from apps.integrations.framework import CRMContact
    from apps.integrations.framework.operations import OutreachExport, OutreachRecipient

    crm = FakeCRMAdapter({}, {"api_key": "k"})
    one = CRMContact("c:1", "Alex")
    assert execute(crm, crm.push_contact, one).data.action == "created"  # type: ignore[union-attr]
    assert execute(crm, crm.push_contact, one).data.action == "unchanged"  # type: ignore[union-attr]
    changed = CRMContact("c:1", "Alex", title="VP Sales")
    assert execute(crm, crm.push_contact, changed).data.action == "updated"  # type: ignore[union-attr]
    assert len(crm.contacts) == 1
    with pytest.raises(ProviderBadRequest):
        CRMContact("bad key!", "Alex")
    out = FakeOutreachAdapter()
    rows = (
        OutreachRecipient("p:1", "A", "a@acme.example.com"),
        OutreachRecipient("p:2", "A2", "a@acme.example.com"),
    )
    receipt = execute(out, out.export_prospects, OutreachExport("l", rows)).data
    assert receipt is not None
    assert (receipt.accepted, receipt.rejected) == (1, 1)
    with pytest.raises(ProviderBadRequest):
        OutreachExport("l", ())


def test_fake_storage_and_automation_edges() -> None:
    store = FakeStorageAdapter()
    with pytest.raises(ProviderNotFound):
        execute(store, store.get_object, "missing/key")
    with pytest.raises(ProviderBadRequest):
        execute(store, store.put_object, "a//b", b"x")
    with pytest.raises(ProviderBadRequest):
        execute(store, store.put_object, "ok", "not bytes")  # type: ignore[arg-type]
    auto = FakeAutomationAdapter()
    with pytest.raises(ProviderNotFound):
        execute(auto, auto.run_status, "run-99")
    with pytest.raises(ProviderBadRequest):
        execute(auto, auto.trigger, "w", {}, idempotency_key="bad key")


def test_fake_people_validates_limit() -> None:
    adapter = FakePeopleDataAdapter()
    with pytest.raises(ProviderBadRequest):
        execute(adapter, adapter.find_people, CompanyRef(name="A"), ["cto"], limit=0)


# ===================================================== bridge and #66 backward compatibility
def test_import_contract_names_still_work_and_are_the_framework_classes() -> None:
    import apps.imports.providers as legacy
    from apps.imports.providers import errors as legacy_errors

    assert ImportsProviderError is ProviderError
    for name in (
        "ProviderAuthError",
        "ProviderBadRequest",
        "ProviderError",
        "ProviderQuotaExceeded",
        "ProviderRateLimited",
        "ProviderRegistryError",
        "ProviderUnavailable",
    ):
        assert (
            getattr(legacy, name) is getattr(legacy_errors, name) is getattr(framework_errors, name)
        )
    for name in legacy.__all__:
        assert hasattr(legacy, name)
    assert legacy.ProviderRateLimited(3).as_dict()["retry_after"] == 3.0


def test_company_adapter_works_through_the_66_import_contract() -> None:
    adapter = FakeCompanyDataAdapter()
    provider = as_company_search_provider(adapter)
    assert provider.name == "fake-company"
    assert provider.capabilities.terms == "manual"
    page = execute_search(provider, CompanySearchRequest(page_size=4))
    assert len(page.items) == 4
    assert page.provider_name == "fake-company"
    assert page.has_more
    assert page.credits_used == 4.0
    legacy = FakeCompanyProvider().search(CompanySearchRequest(page_size=4))
    assert [c.name for c in page.items] == [c.name for c in legacy.items]
    assert page.items[0].to_company_input() is not None
    with pytest.raises(ProviderBadRequest):
        as_company_search_provider(_Probe())


def test_bridge_translates_adapter_errors() -> None:
    adapter = FakeCompanyDataAdapter(fail_with=ProviderRateLimited(9))
    with pytest.raises(ProviderRateLimited) as info:
        execute_search(as_company_search_provider(adapter), CompanySearchRequest())
    assert info.value.retry_after == 9.0


# ================================================================== boundaries and isolation
def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def test_framework_is_pure_python_no_db_no_network_no_sdk() -> None:
    forbidden = (
        "django.db",
        "requests",
        "httpx",
        "urllib3",
        "urllib.request",
        "http.client",
        "aiohttp",
        "socket",
        "openai",
        "anthropic",
    )
    for path in FRAMEWORK.glob("*.py"):
        for name in _imports(path):
            assert not name.startswith(forbidden), f"{path.name} imports {name}"


def test_no_app_code_imports_a_concrete_adapter_outside_the_integrations_package() -> None:
    """Import boundary (#74): business code asks the registry. Concrete adapters live in
    ``apps/integrations/adapters/<vendor>``; only that package may import them."""
    offenders = []
    for root in ("apps", "config"):
        for path in (BACKEND / root).rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if rel.startswith("apps/integrations/"):
                continue
            offenders += [
                f"{rel}: {name}"
                for name in _imports(path)
                if name.startswith("apps.integrations.adapters")
            ]
    assert offenders == []


def test_fakes_are_not_imported_by_production_code() -> None:
    offenders = []
    for path in (BACKEND / "apps").rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith("apps/integrations/framework/"):
            continue
        offenders += [
            f"{rel}: {n}"
            for n in _imports(path)
            if n.startswith("apps.integrations.framework.fakes")
        ]
    assert offenders == []


def test_operations_use_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    for cls in FAKE_ADAPTERS.values():
        creds = dict.fromkeys(cls.config_schema.get("x-credentials", {}).get("required", ()), "k")
        assert cls({}, creds).test_connection().ok
