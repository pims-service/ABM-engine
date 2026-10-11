"""The provider import contract (issue #66): request/result types, errors, registry, fake
provider, compliance guardrails and the idempotency key. DB-free and network-free."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.imports.providers import (
    CompanySearchProvider,
    CompanySearchRequest,
    CompanySearchResult,
    FakeCompanyProvider,
    FilterSupport,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderCapabilities,
    ProviderCompany,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderRegistry,
    ProviderRegistryError,
    ProviderUnavailable,
    default_registry,
    execute_search,
    get_provider,
    list_providers,
    provider_company_key,
    register_provider,
    split_provider_company_key,
    try_provider_company_key,
)
from apps.imports.providers import types as t
from apps.imports.providers.fake import fake_dataset_size
from apps.imports.schema import CompanyInput, InputErrors


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access in a provider contract test")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


@pytest.fixture(autouse=True)
def clean_default_registry() -> Any:
    default_registry.clear()
    yield
    default_registry.clear()


RULES: dict[str, Any] = {
    "schema_version": 1,
    "campaign": {"id": "x", "name": "Gulf logistics"},
    "profile_version": 2,
    "offer": "o",
    "targeting": {
        "countries": ["sa", "AE", "ae"],
        "industries": ["Logistics", " Software "],
        "company_size": {"min": 50, "max": 500},
        "business_model": "b2b",
    },
    "exclusions": {"industries": ["Retail"], "company_types": ["state_owned"]},
    "buyers": {},
    "outreach": {},
    "custom_rules": "",
    "structured_rules": [],
}


# ------------------------------------------------------------------------- request mapping
def test_from_campaign_rules_maps_every_filter() -> None:
    req = CompanySearchRequest.from_campaign_rules(RULES, query="  cargo ", page_size=10)
    assert req.countries == ("SA", "AE")
    assert req.industries == ("Logistics", "Software")
    assert (req.employee_min, req.employee_max) == (50, 500)
    assert req.business_model == "b2b"
    assert req.excluded_industries == ("Retail",)
    assert req.excluded_company_types == ("state_owned",)
    assert req.query == "cargo"
    assert req.page_size == 10
    assert req.limit == t.DEFAULT_LIMIT


def test_from_campaign_rules_open_ended_and_empty() -> None:
    rules = {
        "schema_version": 1,
        "targeting": {"company_size": {"min": None, "max": None}, "business_model": ""},
    }
    req = CompanySearchRequest.from_campaign_rules(rules)
    assert req == CompanySearchRequest()


@pytest.mark.parametrize(
    "rules",
    [
        {"schema_version": 2},
        {},
        "nope",
        {"schema_version": 1, "targeting": "x"},
        {"schema_version": 1, "targeting": {"company_size": 5}},
        {"schema_version": 1, "exclusions": []},
        {"schema_version": 1, "targeting": {"countries": ["ZZ"]}},
    ],
)
def test_from_campaign_rules_rejects_bad_summary(rules: Any) -> None:
    with pytest.raises(ProviderBadRequest):
        CompanySearchRequest.from_campaign_rules(rules)


def test_request_is_frozen() -> None:
    req = CompanySearchRequest()
    with pytest.raises(AttributeError):
        req.query = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"countries": "SA"}, "countries"),
        ({"countries": ["Saudi"]}, "countries"),
        ({"countries": [5]}, "countries"),
        ({"industries": ["x" * 101]}, "industries"),
        ({"industries": [str(i) for i in range(51)]}, "industries"),
        ({"employee_min": -1}, "employee_min"),
        ({"employee_min": True}, "employee_min"),
        ({"employee_max": "9"}, "employee_max"),
        ({"employee_min": 10, "employee_max": 5}, "employee_max"),
        ({"business_model": "nonprofit"}, "business_model"),
        ({"page_size": 0}, "page_size"),
        ({"page_size": t.MAX_PAGE_SIZE + 1}, "page_size"),
        ({"limit": 0}, "limit"),
        ({"limit": t.MAX_LIMIT + 1}, "limit"),
        ({"sort": "random"}, "sort"),
        ({"cursor": ""}, "cursor"),
        ({"cursor": "c" * (t.MAX_CURSOR_LENGTH + 1)}, "cursor"),
        ({"query": "q" * (t.MAX_QUERY_LENGTH + 1)}, "query"),
    ],
)
def test_request_validation(kwargs: dict[str, Any], field: str) -> None:
    with pytest.raises(ProviderBadRequest) as info:
        CompanySearchRequest(**kwargs)
    assert info.value.field == field


def test_pagination_caps_at_bounds() -> None:
    assert CompanySearchRequest(page_size=t.MAX_PAGE_SIZE, limit=t.MAX_LIMIT)
    req = CompanySearchRequest(page_size=80)
    assert req.effective_page_size(30) == 30
    assert req.effective_page_size(500) == 80
    assert req.with_cursor("abc").cursor == "abc"
    assert req.with_cursor(None).cursor is None


# ------------------------------------------------------------------------- errors
ALL_ERRORS = [
    (ProviderAuthError, "provider_auth", False),
    (ProviderRateLimited, "provider_rate_limited", True),
    (ProviderUnavailable, "provider_unavailable", True),
    (ProviderBadRequest, "provider_bad_request", False),
    (ProviderQuotaExceeded, "provider_quota_exceeded", False),
]


@pytest.mark.parametrize(("cls", "code", "retryable"), ALL_ERRORS)
def test_error_hierarchy_codes_and_flags(
    cls: type[ProviderError], code: str, retryable: bool
) -> None:
    err = cls(provider_name="acme", upstream_status=503)
    assert isinstance(err, ProviderError)
    assert (err.code, err.retryable) == (code, retryable)
    assert err.provider_name == "acme"
    assert err.upstream_status == 503
    assert str(err) == err.message
    assert err.as_dict()["code"] == code


def test_error_codes_are_unique() -> None:
    codes = [c for _, c, _ in ALL_ERRORS]
    assert len(set(codes)) == len(codes)


def test_rate_limited_retry_after() -> None:
    assert ProviderRateLimited(12).retry_after == 12.0
    assert ProviderRateLimited(-5).retry_after == 0.0
    assert ProviderRateLimited().retry_after is None
    assert ProviderRateLimited("soon").retry_after is None  # type: ignore[arg-type]
    assert ProviderRateLimited(7).as_dict()["retry_after"] == 7.0


def test_messages_never_carry_credentials_or_upstream_text() -> None:
    secret = "Sk/Live/Abc123"  # pragma: allowlist secret
    body = '{"error":"bad key Sk/Live/Abc123"}'  # pragma: allowlist secret
    for cls, _, _ in ALL_ERRORS:
        err = cls(provider_name=secret, upstream_status=body)
        text = str(err) + repr(err.as_dict())
        assert secret not in text
        assert body not in text
        assert err.upstream_status is None
        assert err.provider_name == ""
    bad = ProviderBadRequest(f"query {secret}")
    assert secret not in str(bad)
    assert bad.field == ""
    assert "Field: query." in str(ProviderBadRequest("query"))


def test_exception_chain_keeps_cause_hidden_from_message() -> None:
    class Upstream(Exception):
        pass

    def fail() -> None:
        try:
            raise Upstream("token=abc")
        except Upstream as exc:
            raise ProviderUnavailable(provider_name="acme") from exc

    with pytest.raises(ProviderUnavailable) as info:
        fail()
    assert "token" not in str(info.value)
    assert isinstance(info.value.__cause__, Upstream)


# ------------------------------------------------------------------------- ProviderCompany
def test_provider_company_to_company_input_valid() -> None:
    pc = ProviderCompany(
        name="  Rimal   Logistics ",
        website="https://www.rimal.example.com/about",
        profile_url="https://www.linkedin.com/company/rimal",
        country="sa",
        provider_id="p1",
    )
    result = pc.to_company_input()
    assert isinstance(result, CompanyInput)
    assert result.name == "Rimal Logistics"
    assert result.domain == "rimal.example.com"
    assert result.country == "SA"


def test_domain_hint_used_when_website_blank() -> None:
    result = ProviderCompany(name="A", domain_hint="a.example.com").to_company_input()
    assert isinstance(result, CompanyInput)
    assert result.domain == "a.example.com"


def test_invalid_provider_rows_become_errors_not_exceptions() -> None:
    bad = ProviderCompany(
        name="",
        website="ftp://x.example.com",
        profile_url="not a url",
        country="Saudi Arabia",
    )
    result = bad.to_company_input()
    assert isinstance(result, InputErrors)
    assert {"name_required", "website_invalid_scheme", "profile_url_invalid"} <= set(result.codes)
    assert result.errors[0].field in {"name", "website", "profile_url", "country"}


def test_untrusted_types_never_raise() -> None:
    odd: Any = ProviderCompany(
        name=123,  # type: ignore[arg-type]
        website=None,  # type: ignore[arg-type]
        employee_count="many",  # type: ignore[arg-type]
        confidence=7,
        extra=None,  # type: ignore[arg-type]
    )
    assert odd.employee_count is None
    assert odd.confidence is None
    assert odd.extra == {}
    assert isinstance(odd.to_company_input(), InputErrors)


def test_require_identifier_flows_through() -> None:
    result = ProviderCompany(name="Only Name").to_company_input(require_identifier=True)
    assert isinstance(result, InputErrors)
    assert result.first_code == "identifier_required"
    assert isinstance(ProviderCompany(name="Only Name").to_company_input(), CompanyInput)


def test_overlong_values_are_reported_or_cut() -> None:
    pc = ProviderCompany(name="n" * 301, description="d" * 900, industry="i" * 300)
    assert len(pc.description) == 500
    assert len(pc.industry) == 200
    result = pc.to_company_input()
    assert isinstance(result, InputErrors)
    assert result.first_code == "name_too_long"


def test_extra_is_capped_scalar_and_scrubbed() -> None:
    extra = {f"k{i}": "v" * 900 for i in range(60)}
    extra["nested"] = {"a": 1}  # type: ignore[assignment]
    extra["api_key"] = "abcdef"  # pragma: allowlist secret
    pc = ProviderCompany(name="A", extra=extra)
    assert len(pc.extra) <= t.EXTRA_MAX_KEYS
    assert "nested" not in pc.extra
    assert all(len(v) <= t.EXTRA_MAX_VALUE_LENGTH for v in pc.extra.values() if isinstance(v, str))
    assert len(repr(pc.extra).encode()) <= t.EXTRA_MAX_BYTES + 200
    assert pc.extra.get("api_key", "[REDACTED]") == "[REDACTED]"


def test_to_raw_data_has_provider_key() -> None:
    pc = ProviderCompany(name="A", provider_id="p9")
    raw = pc.to_raw_data("acme")
    assert raw["provider"] == "acme"
    assert raw["provider_key"] == "acme:p9"
    assert ProviderCompany(name="A").to_raw_data("acme")["provider_key"] is None


# ------------------------------------------------------------------------- result
def test_result_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match=r"."):
        CompanySearchResult(items=(), provider_name="x", retrieved_at=datetime(2026, 1, 1))
    ok = CompanySearchResult(items=[], provider_name="x", retrieved_at=datetime.now(UTC))  # type: ignore[arg-type]
    assert ok.items == ()
    assert not ok.has_more


# ------------------------------------------------------------------------- idempotency key
def test_provider_company_key() -> None:
    assert provider_company_key(" Acme-Data ", " 12345 ") == "acme-data:12345"
    assert provider_company_key("acme", "AbC:1") == "acme:AbC:1"  # id case and colons kept
    assert split_provider_company_key("acme:AbC:1") == ("acme", "AbC:1")
    assert try_provider_company_key("acme", "") is None
    assert try_provider_company_key("Bad Name", "1") is None
    assert try_provider_company_key(None, "1") is None
    assert try_provider_company_key("acme", 5) is None
    for name, pid in [("", "1"), ("a:b", "1"), ("acme", "x" * 201), ("acme", "a\nb")]:
        with pytest.raises(ValueError, match=r"."):
            provider_company_key(name, pid)
    with pytest.raises(ValueError, match=r"."):
        provider_company_key(1, "x")  # type: ignore[arg-type]
    for bad in ["nocolon", "Bad:1", "acme:"]:
        with pytest.raises(ValueError, match=r"."):
            split_provider_company_key(bad)


def test_reimport_is_detected_by_key() -> None:
    provider = FakeCompanyProvider()
    first = provider.search(CompanySearchRequest(page_size=5))
    again = provider.search(CompanySearchRequest(page_size=5))
    keys = [c.provider_key(provider.name) for c in first.items + again.items]
    assert None not in keys
    assert len(set(keys)) == 5  # same companies, same keys


# ------------------------------------------------------------------------- registry
def _caps(terms: str = "licensed_api") -> ProviderCapabilities:
    return ProviderCapabilities(terms=terms)


class Stub:
    def __init__(self, name: str = "stub", terms: Any = "licensed_api") -> None:
        self.name = name
        self.capabilities = _caps(terms)

    def search(self, request: CompanySearchRequest) -> CompanySearchResult:
        return CompanySearchResult(
            items=(), provider_name=self.name, retrieved_at=datetime.now(UTC)
        )


def test_registry_basics() -> None:
    reg = ProviderRegistry()
    assert len(reg) == 0
    b, a = Stub("beta"), Stub("alpha")
    reg.register(b)
    reg.register(a)
    assert reg.get("alpha") is a
    assert [p.name for p in reg.list()] == ["alpha", "beta"]
    assert "beta" in reg
    reg.unregister("beta")
    reg.unregister("beta")
    assert "beta" not in reg
    with pytest.raises(ProviderRegistryError) as info:
        reg.get("beta")
    assert info.value.code == "provider_not_found"


def test_registries_are_independent_and_import_registers_nothing() -> None:
    one, two = ProviderRegistry(), ProviderRegistry()
    one.register(Stub())
    assert len(two) == 0
    assert list_providers() == []


def test_duplicate_and_replace() -> None:
    reg = ProviderRegistry()
    first, second = Stub(), Stub()
    reg.register(first)
    with pytest.raises(ProviderRegistryError) as info:
        reg.register(second)
    assert info.value.code == "duplicate_name"
    reg.register(second, replace=True)
    assert reg.get("stub") is second


def test_module_level_default_registry() -> None:
    p = FakeCompanyProvider()
    register_provider(p)
    assert get_provider("fake") is p
    assert list_providers() == [p]
    assert isinstance(p, CompanySearchProvider)


@pytest.mark.parametrize("name", ["", "Upper", "has space", "-lead", "x" * 65, "a:b"])
def test_invalid_names_refused(name: str) -> None:
    with pytest.raises(ProviderRegistryError) as info:
        ProviderRegistry().register(Stub(name))
    assert info.value.code == "invalid_name"


def test_non_providers_refused() -> None:
    class NoSearch:
        name = "x"
        capabilities = _caps()

    class BadCaps:
        name = "x"
        capabilities = "yes"

        def search(self, request: Any) -> None: ...

    for obj in (object(), NoSearch(), BadCaps(), None):
        with pytest.raises(ProviderRegistryError) as info:
            ProviderRegistry().register(obj)  # type: ignore[arg-type]
        assert info.value.code == "invalid_provider"


# ------------------------------------------------------------------------- compliance
@pytest.mark.parametrize("terms", ["licensed_api", "public_data", "manual", " Manual "])
def test_allowed_terms_register(terms: str) -> None:
    ProviderRegistry().register(Stub(terms=terms))


@pytest.mark.parametrize(
    "terms",
    ["scraping", "web_scraping", "linkedin_scraper", "Crawler", "unauthorized", "unlicensed_api"],
)
def test_scraping_style_terms_rejected(terms: str) -> None:
    reg = ProviderRegistry()
    with pytest.raises(ProviderRegistryError) as info:
        reg.register(Stub(terms=terms))
    assert info.value.code == "terms_forbidden"
    assert len(reg) == 0


@pytest.mark.parametrize(
    ("terms", "code"),
    [
        ("", "terms_missing"),
        (None, "terms_missing"),
        ("whatever", "terms_unknown"),
        ("api", "terms_unknown"),
    ],
)
def test_missing_or_unknown_terms_rejected(terms: Any, code: str) -> None:
    with pytest.raises(ProviderRegistryError) as info:
        ProviderRegistry().register(Stub(terms=terms))
    assert info.value.code == code


def test_profile_urls_are_never_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    # The no_network fixture would raise on any connection attempt; converting and searching
    # with LinkedIn profile URLs must stay pure.
    pc = ProviderCompany(name="A", profile_url="https://www.linkedin.com/company/a")
    assert isinstance(pc.to_company_input(), CompanyInput)
    FakeCompanyProvider().search(CompanySearchRequest())


# ------------------------------------------------------------------------- fake provider
def test_fake_dataset_shape() -> None:
    page = FakeCompanyProvider().search(CompanySearchRequest(page_size=50, limit=50))
    assert fake_dataset_size() == 25 == len(page.items)
    assert page.next_cursor is None
    assert page.total_estimate == 25
    assert any(not c.name.isascii() for c in page.items)  # Arabic names
    for c in page.items:
        assert c.domain_hint.endswith("example.com")
        assert isinstance(c.to_company_input(require_identifier=True), CompanyInput)
    assert len({c.provider_id for c in page.items}) == 25


def test_fake_is_deterministic() -> None:
    a = FakeCompanyProvider().search(CompanySearchRequest(page_size=7))
    b = FakeCompanyProvider().search(CompanySearchRequest(page_size=7))
    assert a.items == b.items
    assert a.retrieved_at == b.retrieved_at


def test_fake_filters() -> None:
    p = FakeCompanyProvider()

    def run(**kw: Any) -> list[ProviderCompany]:
        return list(p.search(CompanySearchRequest(page_size=50, **kw)).items)

    sa = run(countries=["SA"])
    assert sa
    assert {c.country for c in sa} == {"SA"}
    logi = run(industries=["logistics"])
    assert logi
    assert {c.industry for c in logi} == {"Logistics"}
    sized = run(employee_min=100, employee_max=300)
    assert sized
    assert all(100 <= (c.employee_count or 0) <= 300 for c in sized)
    b2c = run(business_model="b2c")
    assert b2c
    assert {c.extra["business_model"] for c in b2c} <= {"b2c", "both"}
    ex = run(excluded_industries=["Logistics"], excluded_company_types=["startup"])
    assert ex
    assert not {c.industry for c in ex} & {"Logistics"}
    assert "startup" not in {c.extra["company_type"] for c in ex}
    assert {c.name for c in run(query="falcon")} == {"Falcon Software"}
    assert run(countries=["SA"], industries=["Retail"]) == []
    combined = run(countries=["AE"], industries=["Logistics"], employee_min=200)
    assert {c.name for c in combined} == {"Harbor Marine Services"}


def test_fake_sort() -> None:
    p = FakeCompanyProvider()
    desc = p.search(CompanySearchRequest(page_size=50, sort="employee_count_desc")).items
    counts = [c.employee_count or 0 for c in desc]
    assert counts == sorted(counts, reverse=True)
    names = [
        c.name.casefold() for c in p.search(CompanySearchRequest(page_size=50, sort="name")).items
    ]
    assert names == sorted(names)


def test_fake_pagination_walks_everything_once() -> None:
    p = FakeCompanyProvider()
    req = CompanySearchRequest(page_size=10)
    seen: list[str] = []
    pages = 0
    while True:
        page = execute_search(p, req)
        seen += [c.provider_id for c in page.items]
        pages += 1
        if page.next_cursor is None:
            break
        req = req.with_cursor(page.next_cursor)
    assert pages == 3
    assert len(seen) == len(set(seen)) == 25


def test_fake_limit_caps_total_across_pages() -> None:
    p = FakeCompanyProvider()
    req = CompanySearchRequest(page_size=4, limit=10)
    total = 0
    while True:
        page = p.search(req)
        total += len(page.items)
        if page.next_cursor is None:
            break
        req = req.with_cursor(page.next_cursor)
    assert total == 10


def test_fake_bad_cursor_and_max_page_size() -> None:
    p = FakeCompanyProvider(max_page_size=5)
    for cursor in ("zzz", "o:", "o:-1", "x:3", "o:" + "9" * 12):
        with pytest.raises(ProviderBadRequest):
            p.search(CompanySearchRequest(cursor=cursor))
    assert len(p.search(CompanySearchRequest(page_size=40)).items) == 5


def test_fake_errors_on_demand() -> None:
    p = FakeCompanyProvider()
    magic = {
        "!auth": ProviderAuthError,
        "!rate_limited": ProviderRateLimited,
        "!unavailable": ProviderUnavailable,
        "!bad_request": ProviderBadRequest,
        "!quota": ProviderQuotaExceeded,
    }
    for query, cls in magic.items():
        with pytest.raises(cls):
            p.search(CompanySearchRequest(query=query))
    p.fail_with = ProviderRateLimited(30)
    with pytest.raises(ProviderRateLimited) as info:
        p.search(CompanySearchRequest())
    assert info.value.retry_after == 30
    assert len(p.calls) == 6


# ------------------------------------------------------------------------- execute_search
def test_execute_search_caps_page_size_and_records() -> None:
    p = FakeCompanyProvider(max_page_size=3)
    page = execute_search(p, CompanySearchRequest(page_size=50))
    assert len(page.items) == 3
    assert p.calls[-1].page_size == 3


def test_execute_search_wraps_unexpected_exceptions() -> None:
    class Boom(Stub):
        def search(self, request: CompanySearchRequest) -> CompanySearchResult:
            raise RuntimeError("token=abc123 secret body")  # pragma: allowlist secret

    with pytest.raises(ProviderUnavailable) as info:
        execute_search(Boom(), CompanySearchRequest())
    assert "abc123" not in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)


def test_execute_search_passes_typed_errors_through() -> None:
    p = FakeCompanyProvider(fail_with=ProviderQuotaExceeded())
    with pytest.raises(ProviderQuotaExceeded):
        execute_search(p, CompanySearchRequest())


def test_execute_search_validates_result() -> None:
    class WrongName(Stub):
        def search(self, request: CompanySearchRequest) -> CompanySearchResult:
            return CompanySearchResult(
                items=(), provider_name="other", retrieved_at=datetime.now(UTC)
            )

    class TooMany(Stub):
        def search(self, request: CompanySearchRequest) -> CompanySearchResult:
            return CompanySearchResult(
                items=(ProviderCompany(name="A"), ProviderCompany(name="B")),
                provider_name=self.name,
                retrieved_at=datetime.now(UTC),
            )

    class NotAResult(Stub):
        def search(self, request: CompanySearchRequest) -> Any:
            return {"items": []}

    for cls in (WrongName, TooMany, NotAResult):
        with pytest.raises(ProviderUnavailable):
            execute_search(cls(), CompanySearchRequest(page_size=1))


def test_execute_search_refuses_provider_without_search() -> None:
    p = Stub()
    p.capabilities = ProviderCapabilities(terms="manual", supports_search=False)
    with pytest.raises(ProviderBadRequest):
        execute_search(p, CompanySearchRequest())


def test_capabilities_defaults() -> None:
    caps = ProviderCapabilities(terms="licensed_api")
    assert caps.supports_search
    assert caps.requires_credentials
    assert caps.supports_filters == FilterSupport()
    assert not any(
        (
            caps.supports_filters.countries,
            caps.supports_filters.industries,
            caps.supports_filters.size,
            caps.supports_filters.business_model,
        )
    )
    assert caps.max_page_size == t.MAX_PAGE_SIZE
