"""Usage tracking, budgets and aggregation (issue #79)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.campaigns.memberships import grant_membership
from apps.core.base import ImmutableRecordError, TenantMismatchError
from apps.integrations import reporting, usage
from apps.integrations.framework import (
    ProviderQuotaExceeded,
    ProviderUnavailable,
    Usage,
)
from apps.integrations.framework.fakes import FakeCompanyDataAdapter
from apps.integrations.framework.normalized import CompanyRef
from apps.integrations.models import ProviderCall, UsageBudget
from apps.integrations.usage import BudgetExceeded, check_budget, record_call, tracked_execute
from tests.factories import make_campaign, make_client, make_user
from tests.factories_core import make_job
from tests.factories_integrations import (
    FAKE_SECRET,
    make_budget,
    make_connection,
    make_provider_call,
)

pytestmark = pytest.mark.django_db

REF = CompanyRef(domain="acme.example.com")


class KeyedFake(FakeCompanyDataAdapter):
    key = "fake_company"  # connection provider keys cannot contain a hyphen


def conn(client: Any = None, **kw: Any) -> Any:
    kw.setdefault("category", "company_data")
    kw.setdefault("provider", "fake_company")
    kw.setdefault("credentials", None)
    return make_connection(client, **kw)


def run(connection: Any, **kw: Any) -> Any:
    adapter = KeyedFake()
    return tracked_execute(connection, adapter, adapter.enrich_company, REF, **kw)


# ------------------------------------------------------------------ one row per call


def test_a_success_writes_one_row_with_usage() -> None:
    c = conn()
    job = make_job(client=c.client)
    result = run(c, job=job)
    row = ProviderCall.objects.get()
    assert result.data is not None
    assert (row.status, row.operation, row.provider) == ("ok", "enrich_company", "fake_company")
    assert row.credits == Decimal("1")
    assert row.job == job
    assert row.client_id == c.client_id
    assert row.latency_ms is not None
    assert not row.cache_hit


def test_a_failure_writes_one_error_row_and_reraises() -> None:
    c = conn()
    adapter = KeyedFake(fail_with=ProviderUnavailable())
    with pytest.raises(ProviderUnavailable):
        tracked_execute(c, adapter, adapter.enrich_company, REF)
    row = ProviderCall.objects.get()
    assert (row.status, row.error_code) == ("error", "provider_unavailable")
    assert row.credits is None


def test_rows_hold_no_payload_or_secret_columns() -> None:
    names = {f.name for f in ProviderCall._meta.get_fields()}
    assert not names & {"payload", "request", "response", "body", "credentials"}
    c = make_connection(credentials={"api_key": FAKE_SECRET}, category="company_data")
    run_row = record_call(c, operation="x", status="ok", usage=Usage(model="m"))
    assert FAKE_SECRET not in str(run_row.__dict__)


def test_rows_are_append_only() -> None:
    row = make_provider_call()
    with pytest.raises(ImmutableRecordError):
        row.save()
    with pytest.raises(ImmutableRecordError):
        ProviderCall.objects.filter(pk=row.pk).update(operation="x")


def test_campaign_of_another_client_is_refused() -> None:
    c = conn()
    other = make_campaign(client=make_client())
    with pytest.raises(TenantMismatchError):
        make_provider_call(c, campaign=other)


def test_logging_failure_is_swallowed_and_counted(caplog: Any) -> None:
    usage.reset_logging_failures()
    c = conn()
    with caplog.at_level(logging.ERROR):
        assert record_call(c, operation="x", status="bogus-status-too-long") is None
    assert usage.logging_failure_count() == 1
    # the call itself is not affected
    assert run(c).data is not None


def test_mismatched_adapter_is_a_programming_error() -> None:
    c = make_connection()  # llm/fake
    adapter = KeyedFake()
    with pytest.raises(ValueError, match="does not belong"):
        tracked_execute(c, adapter, adapter.enrich_company, REF)


# ------------------------------------------------------------------ budgets


def test_no_budget_allows() -> None:
    assert check_budget(conn()).action == "allow"


def test_budget_boundaries_are_exact() -> None:
    c = conn()
    make_budget(c, credit_limit=Decimal("10"), warn_at_percent=80)
    make_provider_call(c, credits=Decimal("7.9999"))
    assert check_budget(c).action == "allow"
    make_provider_call(c, credits=Decimal("0.0001"))
    assert check_budget(c).action == "warn"  # exactly 8 of 10
    make_provider_call(c, credits=Decimal("1.9999"))
    assert check_budget(c).action == "warn"  # 9.9999
    make_provider_call(c, credits=Decimal("0.0001"))
    status = check_budget(c)
    assert status.action == "deny"
    assert status.reason == "credits"


def test_soft_budget_never_blocks() -> None:
    c = conn()
    make_budget(c, credit_limit=Decimal("1"), hard_stop=False)
    make_provider_call(c, credits=Decimal("5"))
    assert check_budget(c).action == "warn"
    assert run(c).data is not None


def test_deny_raises_quota_error_logs_a_row_and_skips_the_provider() -> None:
    c = conn()
    make_budget(c, credit_limit=Decimal("1"))
    make_provider_call(c, credits=Decimal("1"))
    adapter = KeyedFake()
    with pytest.raises(ProviderQuotaExceeded) as info:
        tracked_execute(c, adapter, adapter.enrich_company, REF)
    assert isinstance(info.value, BudgetExceeded)
    assert adapter.calls == []
    assert ProviderCall.objects.filter(error_code="budget_exceeded", status="error").count() == 1


def test_cost_limit_counts_only_its_currency_and_this_month() -> None:
    c = conn()
    make_budget(c, cost_limit=Decimal("5"), currency="USD")
    make_provider_call(c, cost=Decimal("4"), currency="USD")
    make_provider_call(c, cost=Decimal("100"), currency="EUR")
    assert check_budget(c).action == "warn"
    past = timezone.now() - timedelta(days=62)
    make_provider_call(c, cost=Decimal("100"), currency="USD", created_at=past)
    assert check_budget(c).action == "warn"
    make_provider_call(c, cost=Decimal("1"), currency="USD")
    assert check_budget(c).action == "deny"


def test_month_resets_on_the_first_utc() -> None:
    c = conn()
    make_budget(c, credit_limit=Decimal("1"))
    make_provider_call(c, credits=Decimal("1"))
    nxt = timezone.now().astimezone(UTC).replace(day=28) + timedelta(days=5)
    assert check_budget(c, now=nxt.replace(day=1) + timedelta(days=0)).action in {"allow", "deny"}
    start, end = usage.month_bounds(datetime(2026, 12, 31, 23, tzinfo=UTC))
    assert (start.month, end.year, end.month) == (12, 2027, 1)


def test_set_budget_validates_and_audits() -> None:
    c = conn()
    for bad in (
        {"credit_limit": "-1"},
        {"cost_limit": "1"},  # needs currency
        {"credit_limit": "1.00001"},
        {"warn_at_percent": 0},
        {"currency": "US"},
    ):
        with pytest.raises(ValidationError):
            usage.set_budget(c, None, **bad)
    budget = usage.set_budget(c, None, cost_limit="2.5", currency="usd")
    assert (budget.cost_limit, budget.currency) == (Decimal("2.5"), "USD")
    usage.set_budget(c, None, credit_limit=3)
    assert UsageBudget.objects.filter(connection=c).count() == 1
    c.archive()
    with pytest.raises(ValidationError):
        usage.set_budget(c, None, credit_limit=3)


# ------------------------------------------------------------------ aggregation


def test_aggregation_matches_logged_rows() -> None:
    c = conn()
    camp = make_campaign(client=c.client)
    make_provider_call(c, campaign=camp, credits=Decimal("2"), cost=Decimal("0.5"), currency="USD")
    make_provider_call(c, campaign=camp, credits=Decimal("3"))
    make_provider_call(c, campaign=camp, status="error", error_code="x", credits=None)
    make_provider_call(c, credits=Decimal("100"))
    qs = ProviderCall.objects.filter(campaign=camp)
    rows = reporting.summarize(qs, ["provider"])
    by_currency = {r["currency"]: r for r in rows}
    assert by_currency[""]["calls"] == 2
    assert by_currency[""]["error_calls"] == 1
    assert by_currency[""]["credits"] == Decimal("3")
    assert by_currency["USD"]["cost"] == Decimal("0.5")
    assert sum(r["calls"] for r in reporting.totals(qs)) == 3
    assert reporting.run_totals(campaign=camp) == reporting.totals(qs)
    days = reporting.summarize(qs, ["day"])
    assert sum(r["calls"] for r in days) == 3
    with pytest.raises(ValueError, match="unknown"):
        reporting.summarize(qs, ["nope"])
    assert reporting.cost_per_accepted_account(Decimal("3"), 2) == Decimal("1.5")
    assert reporting.cost_per_accepted_account(Decimal("3"), 0) is None


# ------------------------------------------------------------------ API


def api_for(role: str, client: Any) -> APIClient:
    user = make_user()
    grant_membership(client, user, role)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def test_usage_api_roles_filters_summary_and_isolation() -> None:
    c = conn()
    other = conn()
    make_provider_call(c, credits=Decimal("2"))
    make_provider_call(c, status="error", error_code="provider_timeout", credits=None)
    make_provider_call(other)
    for role in ("viewer", "reviewer"):
        assert api_for(role, c.client).get("/api/v1/integrations/usage/").status_code == 403
    api = api_for("manager", c.client)
    data = api.get("/api/v1/integrations/usage/").json()
    assert data["count"] == 2
    assert all(r["client"] == str(c.client_id) for r in data["results"])
    assert "payload" not in data["results"][0]
    assert api.get("/api/v1/integrations/usage/?status=error").json()["count"] == 1
    assert api.get(f"/api/v1/integrations/usage/?client={other.client_id}").json()["count"] == 0
    today = timezone.now().date().isoformat()
    assert (
        api.get(f"/api/v1/integrations/usage/?date_from={today}&date_to={today}").json()["count"]
        == 2
    )
    assert (
        api.get("/api/v1/integrations/usage/?date_from=2030-01-02&date_to=2030-01-01").status_code
        == 400
    )
    summary = api.get("/api/v1/integrations/usage/summary/?group_by=provider,status").json()
    assert summary["group_by"] == ["provider", "status"]
    assert sum(r["calls"] for r in summary["results"]) == 2
    assert api.get("/api/v1/integrations/usage/summary/?group_by=zzz").status_code == 400


def test_budget_api_roles_and_validation() -> None:
    c = conn()
    url = f"/api/v1/integrations/connections/{c.pk}/budget/"
    assert api_for("viewer", c.client).get(url).status_code == 403
    manager = api_for("manager", c.client)
    first = manager.get(url).json()
    assert first["configured"] is False
    assert first["state"] == "allow"
    assert manager.put(url, {"credit_limit": "5"}, format="json").status_code == 403
    admin = api_for("admin", c.client)
    ok = admin.put(url, {"credit_limit": "5", "warn_at_percent": 50}, format="json")
    assert ok.status_code == 200
    assert ok.json()["credit_limit"] == "5.0000"
    assert admin.put(url, {"cost_limit": "5"}, format="json").status_code == 400
    make_provider_call(c, credits=Decimal("5"))
    assert manager.get(url).json()["state"] == "deny"
    stranger = api_for("admin", make_client())
    assert stranger.get(url).status_code == 404
