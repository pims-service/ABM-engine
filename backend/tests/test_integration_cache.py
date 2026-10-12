"""Response cache, raw payload retention and purge (issue #80)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.integrations import payloads
from apps.integrations.cache_policy import (
    NO_CACHE,
    CachePolicy,
    CachePolicyError,
    effective_policy,
    register_cache_policy,
    unregister_cache_policy,
)
from apps.integrations.caching import cached_execute, purge_expired
from apps.integrations.framework import ProviderUnavailable
from apps.integrations.framework.fakes import FakeCompanyDataAdapter, FakeCRMAdapter
from apps.integrations.framework.normalized import CompanyRef
from apps.integrations.models import CachedResponse, ProviderCall, RawPayload
from tests.factories import make_client
from tests.factories_integrations import make_connection

pytestmark = pytest.mark.django_db

REF = CompanyRef(domain="acme.example.com")


class KeyedFake(FakeCompanyDataAdapter):
    key = "fake_company"  # connection provider keys cannot contain a hyphen


class KeyedCRM(FakeCRMAdapter):
    key = "fake_crm"


POLICY = CachePolicy(allow_cache=True, allow_store_raw=True, default_ttl_seconds=3600)


@pytest.fixture(autouse=True)
def _policy() -> Any:
    register_cache_policy("company_data", "fake_company", POLICY)
    yield
    unregister_cache_policy("company_data", "fake_company")


def conn(client: Any = None, **kw: Any) -> Any:
    kw.setdefault("category", "company_data")
    kw.setdefault("provider", "fake_company")
    kw.setdefault("credentials", None)
    return make_connection(client, **kw)


def call(connection: Any, adapter: Any = None, **kw: Any) -> Any:
    adapter = adapter or KeyedFake()
    return cached_execute(connection, adapter, adapter.enrich_company, REF, **kw), adapter


def test_second_identical_call_is_a_free_cache_hit() -> None:
    c = conn()
    first, adapter = call(c)
    second, _ = call(c, adapter)
    assert len(adapter.calls) == 1
    assert not first.provenance.cache_hit
    assert second.provenance.cache_hit
    assert second.data == first.data  # decimals, dates and tuples survive the round trip
    assert second.provenance.retrieved_at == first.provenance.retrieved_at
    assert second.usage.credits == Decimal(0)
    rows = list(ProviderCall.objects.order_by("created_at"))
    assert [r.cache_hit for r in rows] == [False, True]
    assert rows[1].credits == Decimal(0)
    assert rows[1].cost == Decimal(0)


def test_different_request_misses() -> None:
    c = conn()
    _, adapter = call(c)
    cached_execute(c, adapter, adapter.enrich_company, CompanyRef(domain="b.example.com"))
    assert len(adapter.calls) == 2


def test_force_refresh_calls_again_and_keeps_old_records() -> None:
    c = conn()
    _, adapter = call(c)
    call(c, adapter, force_refresh=True)
    assert len(adapter.calls) == 2
    assert ProviderCall.objects.count() == 2
    assert CachedResponse.objects.count() == 1
    assert RawPayload.objects.count() == 0  # nothing attached by this adapter


def test_expiry_and_ttl() -> None:
    c = conn()
    _, adapter = call(c)
    CachedResponse.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    call(c, adapter)
    assert len(adapter.calls) == 2
    ttl = CachedResponse.objects.get().expires_at - timezone.now()
    assert timedelta(minutes=59) < ttl <= timedelta(hours=1)


def test_per_operation_ttl() -> None:
    register_cache_policy(
        "company_data",
        "fake_company",
        CachePolicy(allow_cache=True, operation_ttl_seconds={"enrich_company": 60}),
    )
    c = conn()
    call(c)
    assert CachedResponse.objects.get().expires_at - timezone.now() <= timedelta(seconds=60)


def test_default_policy_never_stores() -> None:
    unregister_cache_policy("company_data", "fake_company")
    assert get_none() is NO_CACHE
    c = conn()
    _, adapter = call(c)
    call(c, adapter)
    assert len(adapter.calls) == 2
    assert CachedResponse.objects.count() == 0


def get_none() -> Any:
    from apps.integrations.cache_policy import get_cache_policy

    return get_cache_policy("company_data", "fake_company")


def test_connection_can_only_narrow_the_policy() -> None:
    c = conn(config={"cache": {"allow_cache": False}})
    _, adapter = call(c)
    call(c, adapter)
    assert len(adapter.calls) == 2
    assert CachedResponse.objects.count() == 0
    widened = conn(config={"cache": {"allow_cache": True, "ttl_seconds": 10}})
    assert effective_policy(widened).default_ttl_seconds == 10
    unregister_cache_policy("company_data", "fake_company")
    assert not effective_policy(widened).allow_cache  # cannot widen a closed provider
    register_cache_policy("company_data", "fake_company", POLICY)
    assert not effective_policy(conn(config={"cache": "garbage"})).allow_cache  # fails closed


def test_policy_validation() -> None:
    with pytest.raises(CachePolicyError):
        CachePolicy(default_ttl_seconds=0)
    with pytest.raises(CachePolicyError):
        CachePolicy(max_payload_bytes=10**9)


def test_cache_is_per_client() -> None:
    a, b = conn(), conn(make_client())
    _, adapter = call(a)
    call(b, adapter)
    assert len(adapter.calls) == 2
    assert CachedResponse.objects.count() == 2
    call(b, adapter)
    assert len(adapter.calls) == 2
    # b cannot read a's entry even with a's key
    key = CachedResponse.objects.get(connection=a).cache_key
    assert not CachedResponse.objects.for_client(b.client).filter(cache_key=key).exists()


def test_failures_are_not_cached() -> None:
    c = conn()
    bad = KeyedFake(fail_with=ProviderUnavailable())
    with pytest.raises(ProviderUnavailable):
        call(c, bad)
    assert CachedResponse.objects.count() == 0


def test_size_cap_skips_storing() -> None:
    register_cache_policy(
        "company_data", "fake_company", CachePolicy(allow_cache=True, max_payload_bytes=50)
    )
    c = conn()
    _, adapter = call(c)
    call(c, adapter)
    assert len(adapter.calls) == 2
    assert CachedResponse.objects.count() == 0


def test_writes_are_never_cached() -> None:

    register_cache_policy("crm", "fake_crm", CachePolicy(allow_cache=True))
    try:
        from apps.integrations.framework.operations import CRMContact

        c = make_connection(
            category="crm", provider="fake_crm", credentials={"api_key": "YOUR_KEY"}
        )
        adapter = KeyedCRM(credentials={"api_key": "YOUR_KEY"})
        contact = CRMContact(external_key="k1", name="A", email="a@example.com")
        cached_execute(c, adapter, adapter.push_contact, contact)
        assert CachedResponse.objects.count() == 0
    finally:
        unregister_cache_policy("crm", "fake_crm")


# ------------------------------------------------------------------ raw payloads and redaction


class RawAdapter(KeyedFake):
    def enrich_company(self, company: CompanyRef) -> Any:
        payloads.attach_raw_payload(
            {
                "name": "Acme",
                "api_key": "YOUR_API_KEY",
                "note": "Authorization: Bearer YOUR_TOKEN1234",
            }  # pragma: allowlist secret
        )
        return super().enrich_company(company)


def test_raw_payload_is_kept_redacted_and_referenced() -> None:
    c = conn()
    result, _ = call(c, RawAdapter())
    raw = RawPayload.objects.get()
    assert raw.call == ProviderCall.objects.get()
    assert result.provenance.raw_ref == f"rawpayload:{raw.pk}"
    assert raw.payload["api_key"] == "[REDACTED]"
    assert "YOUR_TOKEN1234" not in str(raw.payload)
    assert raw.expires_at > timezone.now() + timedelta(days=29)


def test_raw_payload_dropped_when_forbidden() -> None:
    register_cache_policy("company_data", "fake_company", CachePolicy(allow_cache=True))
    c = conn()
    result, _ = call(c, RawAdapter())
    assert RawPayload.objects.count() == 0
    assert result.provenance.raw_ref is None


def test_raw_payload_over_cap_is_dropped() -> None:
    register_cache_policy(
        "company_data", "fake_company", CachePolicy(allow_store_raw=True, max_payload_bytes=10)
    )
    call(conn(), RawAdapter())
    assert RawPayload.objects.count() == 0


def test_attach_outside_a_tracked_call_is_a_noop() -> None:
    assert payloads.attach_raw_payload({"a": 1}) is False


def test_redaction_and_codec_helpers() -> None:
    assert payloads.redact_keys({"x": {"Api_Key": "v", "ok": 1}}) == {
        "x": {"Api_Key": "[REDACTED]", "ok": 1}
    }
    assert payloads.redact_payload({"t": "a\x00b"}) == {"t": "ab"}
    with pytest.raises(payloads.UnsupportedPayload):
        payloads.canonical(object())
    k1 = payloads.cache_key(
        client_id=1,
        connection_id=2,
        provider="p",
        operation="o",
        args=({"a": 1, "b": 2},),
        kwargs={},
    )
    k2 = payloads.cache_key(
        client_id=1,
        connection_id=2,
        provider="p",
        operation="o",
        args=({"b": 2, "a": 1},),
        kwargs={},
    )
    assert k1 == k2


# ------------------------------------------------------------------ purge


def test_purge_removes_expired_and_forbidden_but_keeps_the_log(capsys: Any) -> None:
    c = conn()
    call(c, RawAdapter())
    assert purge_expired().total == 0
    CachedResponse.objects.update(expires_at=timezone.now() - timedelta(days=1))
    RawPayload.objects.update(expires_at=timezone.now() - timedelta(days=1))
    assert purge_expired(dry_run=True).total == 2
    assert CachedResponse.objects.count() == 1
    report = purge_expired()
    assert (report.expired_cache, report.expired_raw) == (1, 1)
    assert ProviderCall.objects.count() == 1


def test_purge_removes_rows_the_policy_no_longer_allows() -> None:
    c = conn()
    call(c, RawAdapter())
    register_cache_policy("company_data", "fake_company", NO_CACHE)
    report = purge_expired()
    assert (report.forbidden_cache, report.forbidden_raw) == (1, 1)


def test_purge_everything_for_a_client_and_the_command() -> None:
    a, b = conn(), conn(make_client())
    call(a, RawAdapter())
    call(b, RawAdapter())
    with pytest.raises(ValueError, match="everything"):
        purge_expired(everything=True)
    call_command("purge_provider_cache", "--dry-run")
    assert CachedResponse.objects.count() == 2
    call_command("purge_provider_cache", "--client", str(a.client_id), "--all")
    assert CachedResponse.objects.filter(client=a.client).count() == 0
    assert CachedResponse.objects.filter(client=b.client).count() == 1
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("purge_provider_cache", "--all")
