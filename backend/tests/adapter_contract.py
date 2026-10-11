"""Reusable contract tests every adapter must pass (issue #74, ADR 0013).

Adapters #82 to #85 (and any later one) import the mixin of their category and subclass it in
their own ``tests/test_*.py``, providing the class under test. Nothing here talks to the network;
the adapter under test must be driven by a fake transport or recorded fixture.

    from tests.adapter_contract import CompanyDataContract

    class TestAcmeCompanyAdapter(CompanyDataContract):
        adapter_cls = AcmeCompanyAdapter

        def make_adapter(self):  # only if it needs a transport or settings
            return AcmeCompanyAdapter({}, {"api_key": "YOUR_API_KEY"}, transport=FakeTransport())

        def make_failing_adapter(self, error):  # make every operation and test_connection raise
            ...

Mixins: ``AdapterContract`` (all categories, included by the others), ``CompanyDataContract``,
``PeopleDataContract``, ``EmailEnrichmentContract``, ``EmailVerificationContract``,
``LLMContract``, ``CRMContract``, ``OutreachContract``, ``StorageContract``,
``AutomationContract``. Pytest does not collect them (no ``Test`` prefix).
"""

from __future__ import annotations

import pickle
import socket
from datetime import timedelta
from typing import Any, ClassVar

import pytest

from apps.imports.providers.types import CompanySearchRequest
from apps.integrations.framework import (
    Adapter,
    AdapterRegistry,
    AdapterResult,
    CompanyRef,
    ConnectionSpec,
    CRMCompany,
    CRMContact,
    IntegrationCategory,
    PersonRef,
    PromptSpec,
    ProviderBadRequest,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    StructuredSchema,
    execute,
    supports,
)
from apps.integrations.framework.base import SecretStr
from apps.integrations.framework.categories import ALLOWED_TERMS, features_of
from apps.integrations.framework.normalized import iter_evidence
from apps.integrations.framework.operations import (
    CATEGORY_BASES,
    OPERATION_FEATURES,
    OutreachExport,
    OutreachRecipient,
)

PLACEHOLDER_SECRET = "YOUR_API_KEY"  # pragma: allowlist secret


class AdapterContract:
    """Behavior every adapter of every category must show."""

    adapter_cls: ClassVar[type[Adapter]]

    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any attempt to open a socket fails the test."""

        def blocked(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("adapter contract tests must not use the network")

        monkeypatch.setattr(socket.socket, "connect", blocked)
        monkeypatch.setattr(socket, "create_connection", blocked)
        monkeypatch.setattr(socket, "getaddrinfo", blocked)

    # ------------------------------------------------------------------ hooks to override
    def make_config(self) -> dict[str, Any]:
        return {}

    def make_credentials(self) -> dict[str, str]:
        required = self.adapter_cls.config_schema.get("x-credentials", {}).get("required", ())
        return dict.fromkeys(required, PLACEHOLDER_SECRET)

    def make_spec(self) -> ConnectionSpec:
        cls = self.adapter_cls
        return ConnectionSpec(cls.category, cls.key, self.make_config(), self.make_credentials())

    def make_adapter(self) -> Any:
        return self.adapter_cls(self.make_config(), self.make_credentials())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        """An adapter whose operations and ``test_connection`` raise ``error``; ``None`` skips."""
        adapter = self.make_adapter()
        if hasattr(adapter, "fail_with"):
            adapter.fail_with = error
            return adapter
        return None

    # ------------------------------------------------------------------ declarations
    def test_declarations(self) -> None:
        cls = self.adapter_cls
        assert isinstance(cls.category, IntegrationCategory)
        assert issubclass(cls, CATEGORY_BASES[cls.category])
        assert cls.display_name.strip()
        assert cls.terms in ALLOWED_TERMS
        assert cls.capabilities.features <= features_of(cls.category)

    def test_declared_features_are_implemented(self) -> None:
        cls = self.adapter_cls
        base = CATEGORY_BASES[cls.category]
        for operation, feature in OPERATION_FEATURES.items():
            if not hasattr(base, operation):
                continue
            implemented = getattr(cls, operation) is not getattr(base, operation)
            declared = cls.capabilities.supports(feature)
            assert implemented == declared, f"{operation}: declare {feature} iff implemented"

    def test_supports_negotiation(self) -> None:
        cls = self.adapter_cls
        for feature in features_of(cls.category):
            assert supports(cls, feature) == cls.capabilities.supports(feature)
        with pytest.raises(ValueError, match="not a feature"):
            supports(cls, "send")

    # ------------------------------------------------------------------ config
    def test_registers_and_resolves(self) -> None:
        registry = AdapterRegistry()
        registry.register(self.adapter_cls)
        adapter = registry.resolve(self.make_spec())
        assert isinstance(adapter, self.adapter_cls)
        assert (
            registry.get_class(self.adapter_cls.category, self.adapter_cls.key) is self.adapter_cls
        )

    def test_valid_config_validates(self) -> None:
        result = self.adapter_cls.validate_config(self.make_config(), self.make_credentials())
        assert result.ok, result.issues

    def test_missing_required_credentials_are_reported_by_name(self) -> None:
        required = self.adapter_cls.config_schema.get("x-credentials", {}).get("required", ())
        if not required:
            pytest.skip("adapter needs no credentials")
        result = self.adapter_cls.validate_config(self.make_config(), {})
        assert {i.field for i in result.issues} >= set(required)
        with pytest.raises(ProviderNotConfigured) as info:
            self.adapter_cls(self.make_config(), {})
        assert set(required) <= set(info.value.fields)

    def test_unknown_setting_and_credential_are_rejected(self) -> None:
        result = self.adapter_cls.validate_config(
            {**self.make_config(), "nope": 1}, {**self.make_credentials(), "other": "x"}
        )
        codes = {(i.field, i.code) for i in result.issues}
        assert ("nope", "unknown_field") in codes
        assert ("other", "unknown_credential") in codes

    def test_config_schema_never_holds_secret_values(self) -> None:
        text = repr(self.adapter_cls.config_schema)
        assert PLACEHOLDER_SECRET not in text

    # ------------------------------------------------------------------ secrets
    def test_credentials_never_leak(self) -> None:
        secret = "Zz-Never-Show-Me-123"  # pragma: allowlist secret
        names = self.adapter_cls.config_schema.get("x-credentials", {}).get("required", ())
        creds = dict.fromkeys(names, secret)
        adapter = self.adapter_cls(self.make_config(), creds)
        spec = ConnectionSpec(
            self.adapter_cls.category, self.adapter_cls.key, self.make_config(), creds
        )
        shown = repr(adapter) + str(adapter) + repr(spec) + repr(adapter.test_connection())
        shown += repr(adapter.test_connection().as_dict())
        assert secret not in shown
        for name in names:
            assert secret not in repr(adapter.credential(name))
            with pytest.raises(TypeError):
                pickle.dumps(adapter.credential(name))
            assert isinstance(adapter.credential(name), SecretStr)

    # ------------------------------------------------------------------ test_connection
    def test_connection_succeeds(self) -> None:
        result = self.make_adapter().test_connection()
        assert result.ok is True
        assert result.error_code is None
        assert result.message
        assert result.checked_at.utcoffset() is not None
        assert result.latency_ms is None or result.latency_ms >= 0

    def test_connection_failure_is_safe(self) -> None:
        adapter = self.make_failing_adapter(ProviderRateLimited(5, provider_name="acme"))
        if adapter is None:
            pytest.skip("no failing adapter available")
        result = adapter.test_connection()
        assert result.ok is False
        assert result.error_code == "provider_rate_limited"
        assert result.message == ProviderRateLimited.message

    def test_operation_errors_are_typed(self) -> None:
        adapter = self.make_failing_adapter(ProviderRateLimited(5))
        if adapter is None:
            pytest.skip("no failing adapter available")
        for operation, args in self.operation_calls(adapter):
            with pytest.raises(ProviderError) as info:
                execute(adapter, getattr(adapter, operation), *args)
            assert info.value.code == "provider_rate_limited"

    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        """(operation name, args) for each declared operation; category mixins override."""
        return []

    # ------------------------------------------------------------------ results
    def check_result(self, adapter: Adapter, result: AdapterResult[Any]) -> None:
        """The envelope rules every operation result obeys."""
        assert isinstance(result, AdapterResult)
        prov = result.provenance
        assert prov.provider == adapter.key
        assert prov.category == adapter.category
        assert prov.terms == adapter.terms
        assert prov.retrieved_at.utcoffset() == timedelta(0) or prov.retrieved_at.tzinfo
        if any(True for _ in iter_evidence(result.data)):
            assert prov.has_source, "evidence needs a source"
        assert result.usage.latency_ms is not None  # filled by execute

    def test_undeclared_operation_is_refused(self) -> None:
        adapter = self.make_adapter()
        base = CATEGORY_BASES[adapter.category]
        for operation, feature in OPERATION_FEATURES.items():
            if hasattr(base, operation) and not adapter.capabilities.supports(feature):
                with pytest.raises(ProviderBadRequest):
                    execute(adapter, getattr(adapter, operation))
                return
        pytest.skip("adapter declares every feature")


# ====================================================================== per category
class CompanyDataContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        calls: list[tuple[str, tuple[Any, ...]]] = []
        if adapter.capabilities.supports("search"):
            calls.append(("search", (CompanySearchRequest(),)))
        if adapter.capabilities.supports("enrich_company"):
            calls.append(("enrich_company", (CompanyRef(domain="acme.example.com"),)))
        return calls

    def test_search_page(self) -> None:
        adapter = self.make_adapter()
        if not adapter.capabilities.supports("search"):
            pytest.skip("no search")
        result = execute(adapter, adapter.search, CompanySearchRequest(page_size=5))
        self.check_result(adapter, result)
        assert result.data is not None
        assert len(result.data.items) <= min(5, adapter.capabilities.max_page_size)

    def test_enrich_unknown_company_is_none_not_invented(self) -> None:
        adapter = self.make_adapter()
        if not adapter.capabilities.supports("enrich_company"):
            pytest.skip("no enrich")
        ref = CompanyRef(domain="no-such-company.invalid.test")
        result = execute(adapter, adapter.enrich_company, ref)
        self.check_result(adapter, result)
        if result.data is not None:
            assert result.data.employee_count is None  # nothing fabricated about an unknown firm


class PeopleDataContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("find_people", (CompanyRef(domain="acme.example.com"), ["head of sales"]))]

    def test_find_people(self) -> None:
        adapter = self.make_adapter()
        ref = CompanyRef(domain="acme.example.com")
        result = execute(adapter, adapter.find_people, ref, ["head of sales"], limit=3)
        self.check_result(adapter, result)
        assert result.data is not None
        assert len(result.data) <= 3
        assert all(p.email is None or p.email_status in {"unknown", "valid"} for p in result.data)

    def test_roles_are_validated(self) -> None:
        adapter = self.make_adapter()
        with pytest.raises(ProviderBadRequest):
            execute(adapter, adapter.find_people, CompanyRef(name="Acme"), [])


class EmailEnrichmentContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("find_email", (PersonRef("Alex Example"), "acme.example.com"))]

    def test_find_email(self) -> None:
        adapter = self.make_adapter()
        result = execute(
            adapter,
            adapter.find_email,
            PersonRef("Alex Example"),
            "acme.example.com",
        )
        self.check_result(adapter, result)
        assert result.data is not None
        assert result.data.email is None or result.data.email.endswith("@acme.example.com")


class EmailVerificationContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("verify", ("alex@acme.example.com",))]

    def test_verify(self) -> None:
        adapter = self.make_adapter()
        result = execute(adapter, adapter.verify, "alex@acme.example.com")
        self.check_result(adapter, result)
        assert result.data is not None
        assert result.data.email == "alex@acme.example.com"
        assert result.data.status in {
            "valid",
            "invalid",
            "risky",
            "catch_all",
            "disposable",
            "unknown",
        }


class LLMContract(AdapterContract):
    schema: ClassVar[StructuredSchema[Any]] = StructuredSchema(
        "echo", {"type": "object"}, lambda raw: dict(raw)
    )

    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("generate_structured", (PromptSpec("p", "1", "hello"), self.schema))]

    def test_generate_structured_reports_usage(self) -> None:
        adapter = self.make_adapter()
        prompt = PromptSpec("contract", "1", "say hello")
        result = execute(adapter, adapter.generate_structured, prompt, self.schema)
        self.check_result(adapter, result)
        assert isinstance(result.data, dict)
        assert result.usage.model
        assert result.usage.input_tokens is not None
        assert result.usage.output_tokens is not None


class CRMContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("push_contact", (CRMContact("contact:1", "Alex Example"),))]

    def test_push_is_idempotent(self) -> None:
        adapter = self.make_adapter()
        contact = CRMContact("contact:1", "Alex Example", email="alex@acme.example.com")
        first = execute(adapter, adapter.push_contact, contact)
        second = execute(adapter, adapter.push_contact, contact)
        self.check_result(adapter, first)
        assert first.data is not None
        assert second.data is not None
        assert first.data.action == "created"
        assert second.data.action in {"unchanged", "updated"}
        assert second.data.remote_id == first.data.remote_id

    def test_push_company_is_idempotent(self) -> None:
        adapter = self.make_adapter()
        company = CRMCompany("company:1", "Acme", domain="acme.example.com")
        first = execute(adapter, adapter.push_company, company)
        second = execute(adapter, adapter.push_company, company)
        assert first.data is not None
        assert second.data is not None
        assert second.data.remote_id == first.data.remote_id


class OutreachContract(AdapterContract):
    def _export(self) -> OutreachExport:
        return OutreachExport(
            "April list", (OutreachRecipient("p:1", "Alex Example", "alex@acme.example.com"),)
        )

    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("export_prospects", (self._export(),))]

    def test_export_only(self) -> None:
        adapter = self.make_adapter()
        assert adapter.capabilities.features == {"export"}  # V1: no sending
        result = execute(adapter, adapter.export_prospects, self._export())
        self.check_result(adapter, result)
        assert result.data is not None
        assert result.data.accepted == 1


class StorageContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("put_object", ("raw/a.json", b"{}"))]

    def test_put_get_delete(self) -> None:
        adapter = self.make_adapter()
        put = execute(adapter, adapter.put_object, "raw/a.json", b"{}", "application/json")
        assert put.data is not None
        assert put.data.size == 2
        got = execute(adapter, adapter.get_object, "raw/a.json")
        assert got.data == b"{}"
        assert execute(adapter, adapter.delete_object, "raw/a.json").data is True
        assert execute(adapter, adapter.delete_object, "raw/a.json").data is False

    def test_unsafe_key_is_refused(self) -> None:
        adapter = self.make_adapter()
        with pytest.raises(ProviderBadRequest):
            execute(adapter, adapter.put_object, "../etc/passwd", b"x")


class AutomationContract(AdapterContract):
    def operation_calls(self, adapter: Adapter) -> list[tuple[str, tuple[Any, ...]]]:
        return [("run_status", ("run-1",))]

    def test_trigger_is_idempotent(self) -> None:
        adapter = self.make_adapter()
        a = execute(adapter, adapter.trigger, "enrich", {"company": 1}, idempotency_key="job:1")
        b = execute(adapter, adapter.trigger, "enrich", {"company": 1}, idempotency_key="job:1")
        assert a.data is not None
        assert b.data is not None
        assert a.data.run_id == b.data.run_id
        status = execute(adapter, adapter.run_status, a.data.run_id)
        assert status.data is not None
        assert status.data.status in {"queued", "running", "succeeded", "failed"}
