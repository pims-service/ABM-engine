"""The research collectors, the run that orders them and their endpoints (issues #91 to #101)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.companies.models import CompanyResearch, DataSource, DataSourceType
from apps.companies.services import archive_company
from apps.core.models import Job, JobStatus
from apps.integrations.company_data_connection import CompanyDataUnavailable, get_adapter
from apps.integrations.framework import CompanyRef, NormalizedCompany
from apps.integrations.framework.envelope import AdapterResult
from apps.integrations.framework.fakes import FakeCompanyDataAdapter
from apps.integrations.framework.normalized import DepartmentHeadcount, NormalizedJobPosting
from apps.research import orchestrator
from apps.research.collect.merge import merged_profile
from apps.research.collect.news import FakeNewsSource, NewsItem, collect_news, tag_item
from apps.research.collect.profile import collect_profile
from apps.research.collect.signals import collect_signals
from apps.research.evidence import signal_sources
from apps.research.history import diff_between, history_for
from apps.research.models import Signal
from tests.factories import make_campaign, make_client, make_company, make_research
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]


def acme(**overrides: Any) -> Any:
    client = overrides.pop("client", None) or make_client()
    return make_company(
        campaign=make_campaign(client=client), website="https://acme.example.com", **overrides
    )


class OtherFake(FakeCompanyDataAdapter):
    """A second provider that reports the same facts."""

    key = "other-company"


class PostingsFake(FakeCompanyDataAdapter):
    """A provider whose job postings are given by the test."""

    def __init__(self, postings: tuple[NormalizedJobPosting, ...]) -> None:
        super().__init__()
        self.postings = postings

    def list_job_postings(self, company: CompanyRef) -> Any:
        result = super().list_job_postings(company)
        return AdapterResult(data=self.postings, provenance=result.provenance)


class BiggerFake(FakeCompanyDataAdapter):
    """The same provider a month later: the company has grown."""

    def enrich_company(self, company: CompanyRef) -> Any:
        result = super().enrich_company(company)
        data = result.data
        assert data is not None
        bigger = NormalizedCompany(
            name=data.name,
            domain=data.domain,
            employee_count=150,
            departments=(DepartmentHeadcount("sales", 20),),
        )
        return AdapterResult(data=bigger, provenance=result.provenance)


# ------------------------------------------------------------------ connection


def test_the_fake_company_data_adapter_is_only_for_development() -> None:
    client = make_client()
    assert get_adapter(client.pk).key == "fake-company"
    with override_settings(PROVIDER_FAKE_ENABLED=False), pytest.raises(CompanyDataUnavailable):
        get_adapter(client.pk)


# ------------------------------------------------------------------ profile


def test_the_profile_is_stored_with_its_source_and_lists_what_is_missing() -> None:
    company = acme()
    outcome = collect_profile(company, FakeCompanyDataAdapter())
    assert outcome.status == "created"
    snapshot = outcome.snapshot
    assert snapshot is not None
    assert snapshot.data_source.type == DataSourceType.PROVIDER
    assert snapshot.data_source.name == "fake-company"
    assert (snapshot.employee_count, snapshot.sales_headcount) == (120, 12)
    assert snapshot.bd_headcount is None
    assert snapshot.marketing_headcount is None
    assert "industry" in outcome.missing_fields
    assert "employee_count" not in outcome.missing_fields


def test_growth_comes_from_the_growth_module_with_the_provider_figures_as_fallback() -> None:
    snapshot = collect_profile(acme(), FakeCompanyDataAdapter()).snapshot
    assert snapshot is not None
    assert str(snapshot.headcount_change_3m) == "4.20"
    assert snapshot.headcount_change_12m is None  # not given, not invented
    growth = snapshot.department_growth
    assert growth is not None
    assert growth["version"] == 1
    assert Decimal(growth["departments"]["sales"]["3m"]["percent"]) == Decimal("8.5")


def test_collecting_again_with_unchanged_data_changes_nothing() -> None:
    company = acme()
    first = collect_profile(company, FakeCompanyDataAdapter())
    again = collect_profile(company, FakeCompanyDataAdapter())
    assert again.status == "unchanged"
    assert again.snapshot == first.snapshot
    assert CompanyResearch.objects.filter(company=company).count() == 1
    assert DataSource.objects.filter(client=company.client).count() == 1


def test_changed_data_adds_a_snapshot_and_keeps_the_old_one() -> None:
    company = acme()
    collect_profile(company, FakeCompanyDataAdapter())
    changed = collect_profile(company, BiggerFake())
    assert changed.status == "created"
    assert CompanyResearch.objects.filter(company=company).count() == 2


def test_a_company_the_provider_does_not_know_stores_nothing() -> None:
    company = make_company(website="https://unknown.test")
    outcome = collect_profile(company, FakeCompanyDataAdapter())
    assert outcome.status == "empty"
    assert "employee_count" in outcome.missing_fields
    assert not CompanyResearch.objects.filter(company=company).exists()
    assert not DataSource.objects.filter(client=company.client).exists()


# ------------------------------------------------------------------ signals


def test_provider_signals_funding_and_hiring_become_signals_with_sources() -> None:
    company = acme()
    profile = collect_profile(company, FakeCompanyDataAdapter())
    outcome = collect_signals(company, FakeCompanyDataAdapter(), profile)
    assert outcome.created == 3  # the provider's sales_hiring, the funding round, open sales roles
    types = sorted(Signal.objects.filter(company=company).values_list("type", "event_date"))
    assert types == [
        ("funding", date(2025, 6, 1)),
        ("sales_hiring", date(2025, 12, 1)),
        ("sales_hiring", date(2025, 12, 15)),
    ]
    assert all(s.data_source.name == "fake-company" for s in Signal.objects.all())
    assert all(s.expires_at is not None for s in Signal.objects.all())


def test_the_same_source_reporting_again_changes_nothing() -> None:
    company = acme()
    profile = collect_profile(company, FakeCompanyDataAdapter())
    collect_signals(company, FakeCompanyDataAdapter(), profile)
    sources_before = DataSource.objects.count()
    again = collect_signals(company, FakeCompanyDataAdapter(), profile)
    assert (again.created, again.corroborated, again.duplicates) == (0, 0, 3)
    assert Signal.objects.count() == 3
    assert DataSource.objects.count() == sources_before


def test_a_second_source_for_an_event_supersedes_it_and_keeps_both_sources() -> None:
    company = acme()
    collect_signals(
        company, FakeCompanyDataAdapter(), collect_profile(company, FakeCompanyDataAdapter())
    )
    other = OtherFake()
    outcome = collect_signals(company, other, collect_profile(company, other))
    assert outcome.corroborated == 3
    assert outcome.created == 0
    current = Signal.objects.current().filter(company=company)
    assert current.count() == 3  # each event once
    funding = current.get(type="funding")
    assert {s.name for s in signal_sources(funding)} == {"fake-company", "other-company"}


def test_postings_are_counted_by_department_and_unclear_titles_are_not_guessed() -> None:
    company = acme()
    day = date(2026, 10, 20)
    adapter = PostingsFake(
        (
            NormalizedJobPosting(title="Sales Executive", posted_date=day),
            NormalizedJobPosting(title="Senior Sales Manager", posted_date=day),
            NormalizedJobPosting(title="Business Development Manager", posted_date=day),
            NormalizedJobPosting(title="Software Engineer", posted_date=day),
            NormalizedJobPosting(title="Head of Growth", posted_date=day),
            NormalizedJobPosting(title="مدير مبيعات"),  # no date
        )
    )
    outcome = collect_signals(company, adapter, None)
    assert outcome.postings_seen == 6
    assert outcome.postings_unclassified == 2
    assert outcome.undated == 1
    stored = {s.type: s.evidence for s in Signal.objects.filter(company=company)}
    assert set(stored) == {"sales_hiring", "bd_hiring"}
    assert stored["sales_hiring"].startswith("2 open sales roles")
    assert stored["bd_hiring"].startswith("1 open business development role:")


def test_an_undated_event_is_counted_but_never_stored_and_a_future_one_is_refused() -> None:
    company = acme()
    future = timezone.now().date() + timedelta(days=30)
    adapter = PostingsFake(
        (
            NormalizedJobPosting(title="Sales Manager"),
            NormalizedJobPosting(title="Sales Executive", posted_date=future),
        )
    )
    outcome = collect_signals(company, adapter, None)
    assert (outcome.undated, outcome.future, outcome.created) == (1, 1, 0)
    assert not Signal.objects.filter(company=company).exists()


# ------------------------------------------------------------------ news


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Acme raises $20m Series B", "funding"),
        ("Acme appoints new CMO", "new_leadership"),
        ("Acme opens a new office in Riyadh", "new_office"),
        ("Acme expands into Egypt", "market_expansion"),
        ("Acme launches a new payments platform", "new_product"),
        ("Acme partners with Aramex", "major_partnership"),
        ("Acme wins an award", None),
        ("", None),
    ],
)
def test_news_is_tagged_by_keyword(title: str, expected: str | None) -> None:
    assert tag_item(NewsItem(title=title, url="https://n.example.com", publisher="N")) == expected


def test_news_is_skipped_when_no_source_is_configured() -> None:
    outcome = collect_news(acme())
    assert "No news source" in outcome.skipped_reason
    assert not Signal.objects.exists()


@override_settings(RESEARCH_NEWS_PROVIDER="fake")
def test_news_from_the_configured_source_becomes_signals_once() -> None:
    company = acme()
    first = collect_news(company)
    assert first.created == 1
    signal = Signal.objects.get()
    assert signal.type == "funding"
    assert signal.data_source.type == DataSourceType.NEWS
    assert signal.data_source.url.startswith("https://news.example.com/")
    assert signal.data_source.evidence_date == signal.event_date
    again = collect_news(company)
    assert again.duplicates == 1
    assert Signal.objects.count() == 1


def test_news_outside_the_window_without_a_link_or_a_date_is_left_out() -> None:
    company = acme()
    today = timezone.now().date()
    items = [
        NewsItem(
            "Acme raises Series A", "https://n.example.com/old", "N", today - timedelta(days=900)
        ),
        NewsItem("Acme raises Series B", "", "N", today),
        NewsItem("Acme raises Series C", "https://n.example.com/nodate", "N", None),
        NewsItem("Acme wins an award", "https://n.example.com/award", "N", today),
        NewsItem("Acme raises Series D", "https://n.example.com/ok", "N", today),
    ]
    outcome = collect_news(company, FakeNewsSource(items))
    assert (outcome.out_of_window, outcome.unusable, outcome.undated, outcome.untagged) == (
        1,
        1,
        1,
        1,
    )
    assert outcome.created == 1


@override_settings(RESEARCH_NEWS_PROVIDER="fake", PROVIDER_FAKE_ENABLED=False)
def test_the_fake_news_source_needs_the_development_switch() -> None:
    assert "only available in development" in collect_news(acme()).skipped_reason


# ------------------------------------------------------------------ merged profile


def test_the_merged_profile_prefers_the_better_kind_of_source_and_shows_the_others() -> None:
    company = make_company()
    now = timezone.now()
    website = DataSource.objects.create(
        client=company.client,
        type=DataSourceType.WEBSITE,
        name="acme.example.com",
        url="https://acme.example.com",
    )
    provider = DataSource.objects.create(
        client=company.client, type=DataSourceType.PROVIDER, name="fake-company"
    )
    make_research(
        company=company,
        data_source=provider,
        researched_at=now - timedelta(days=5),
        employee_count=120,
        industry=None,
    )
    make_research(
        company=company,
        data_source=website,
        researched_at=now,
        employee_count=300,
        industry="Logistics",
    )
    rows = {r["field"]: r for r in merged_profile(company)}
    assert rows["employee_count"]["value"] == "120"  # provider outranks website, though older
    assert rows["employee_count"]["origin"]["name"] == "fake-company"
    assert [a["value"] for a in rows["employee_count"]["alternatives"]] == ["300"]
    assert rows["industry"]["value"] == "Logistics"
    assert rows["industry"]["alternatives"] == []
    with override_settings(RESEARCH_SOURCE_PRECEDENCE=("website", "provider")):
        flipped = {r["field"]: r for r in merged_profile(company)}
    assert flipped["employee_count"]["value"] == "300"


# ------------------------------------------------------------------ the run


def run_for(company: Any, user: Any = None) -> Job:
    return orchestrator.request_research(company, user).job


def test_a_run_has_one_item_per_step_and_ends_with_what_each_found() -> None:
    company = acme()
    job = run_for(company)
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    steps = {(i.result or {})["status"]: i for i in job.items.all()}
    assert set(steps) == {"created", "done", "skipped"}  # profile, signals, news (no source)
    assert Signal.objects.filter(company=company).count() == 3
    assert CompanyResearch.objects.filter(company=company).count() == 1


def test_a_step_that_cannot_run_does_not_stop_the_others() -> None:
    company = acme()
    with mock.patch(
        "apps.research.collect.run.get_adapter",
        side_effect=CompanyDataUnavailable("Connect a provider."),
    ):
        job = run_for(company)
    job.refresh_from_db()
    assert job.status == JobStatus.PARTIAL
    failed = sorted(i.error for i in job.items.filter(status="failed"))
    assert failed == ["Connect a provider.", "Connect a provider."]
    assert job.items.filter(status="succeeded").count() == 1  # news was skipped, not failed


def test_a_step_that_crashes_is_recorded_and_the_rest_still_run() -> None:
    company = acme()
    with mock.patch("apps.research.collect.run.collect_profile", side_effect=RuntimeError("boom")):
        job = run_for(company)
    job.refresh_from_db()
    assert job.status == JobStatus.PARTIAL
    assert job.items.filter(error__startswith="RuntimeError: boom").count() == 1
    assert Signal.objects.filter(company=company).exists()  # signals still ran, from postings


def test_a_company_with_a_run_in_progress_is_not_queued_twice() -> None:
    company = acme()
    with mock.patch("apps.research.orchestrator.async_task", return_value="t1"):
        first = orchestrator.request_research(company)
        second = orchestrator.request_research(company)
    assert first.created
    assert not second.created
    assert first.job.pk == second.job.pk
    assert Job.objects.filter(type="company_research").count() == 1


def test_an_archived_company_is_not_researched() -> None:
    company = acme()
    archive_company(company)
    with pytest.raises(ValidationError):
        orchestrator.request_research(company)


# ------------------------------------------------------------------ endpoints


def test_the_research_endpoints_start_a_run_and_show_its_result() -> None:
    client = make_client()
    company = acme(client=client)
    manager = api_for("manager", client)
    started = manager.post(f"/api/v1/companies/{company.pk}/research/")
    assert started.status_code == 202
    data = started.json()
    assert data["already_queued"] is False
    assert [s["step"] for s in data["run"]["steps"]] == ["profile", "signals", "news"]
    runs = manager.get(f"/api/v1/companies/{company.pk}/research-runs/").json()
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["steps"][2]["result"]["skipped_reason"]
    evidence = manager.get(f"/api/v1/companies/{company.pk}/evidence/").json()
    # one row per provider answer (profile, funding signals, job postings), each with its own
    # reference, all from the same provider
    assert {e["name"] for e in evidence} == {"fake-company"}
    assert len({e["provider_reference"] for e in evidence}) == len(evidence)
    assert {b["kind"] for e in evidence for b in e["backs"]} == {"research_snapshot", "signal"}
    profile = manager.get(f"/api/v1/companies/{company.pk}/profile/").json()
    assert {r["field"] for r in profile} >= {"employee_count", "sales_headcount"}


def test_a_viewer_can_read_research_but_not_start_it() -> None:
    client = make_client()
    company = acme(client=client)
    viewer = api_for("viewer", client)
    assert viewer.post(f"/api/v1/companies/{company.pk}/research/").status_code == 403
    for tail in ("research-runs", "evidence", "profile"):
        assert viewer.get(f"/api/v1/companies/{company.pk}/{tail}/").status_code == 200


def test_another_clients_research_is_not_visible() -> None:
    company = acme()
    outsider = api_for("manager", make_client())
    assert outsider.get(f"/api/v1/companies/{company.pk}/evidence/").status_code == 404
    assert outsider.post(f"/api/v1/companies/{company.pk}/research/").status_code == 404


# ------------------------------------------------------------------ history window and diff


def _three_snapshots(company: Any) -> list[Any]:
    now = timezone.now()
    return [
        make_research(
            company=company, researched_at=now - timedelta(days=days), employee_count=count
        )
        for days, count in ((90, 100), (45, 120), (1, 140))
    ]


def test_the_history_window_hides_old_snapshots_but_never_the_newest() -> None:
    company = make_company()
    old, mid, new = _three_snapshots(company)
    assert [r["id"] for r in history_for(company)] == [new.pk, mid.pk, old.pk]
    with override_settings(RESEARCH_HISTORY_DAYS=30):
        shown = history_for(company)
        assert [r["id"] for r in shown] == [new.pk]
        assert shown[0]["first"] is False  # still compared with the hidden one before it
        assert shown[0]["changes"][0]["old"] == "120"
    with override_settings(RESEARCH_HISTORY_DAYS=1):
        newest_only = history_for(company)
        assert [r["id"] for r in newest_only] == [new.pk]
    assert CompanyResearch.objects.filter(company=company).count() == 3  # nothing was deleted


def test_any_two_snapshots_can_be_compared() -> None:
    company = make_company()
    old, _mid, new = _three_snapshots(company)
    result = diff_between(company, old.pk, new.pk)
    assert result is not None
    assert [(c["field"], c["old"], c["new"]) for c in result["changes"]] == [
        ("employee_count", "100", "140")
    ]
    assert diff_between(company, old.pk, make_research().pk) is None  # another company's


def test_the_diff_endpoint_checks_its_input() -> None:
    client = make_client()
    company = acme(client=client)
    old, _, new = _three_snapshots(company)
    viewer = api_for("viewer", client)
    url = f"/api/v1/companies/{company.pk}/research-history/diff/"
    ok = viewer.get(url, {"from": str(old.pk), "to": str(new.pk)})
    assert ok.status_code == 200
    assert ok.json()["changes"][0]["new"] == "140"
    assert viewer.get(url, {"from": str(old.pk)}).status_code == 400
    assert viewer.get(url, {"from": "nope", "to": str(new.pk)}).status_code == 400
    foreign = make_research()
    assert viewer.get(url, {"from": str(old.pk), "to": str(foreign.pk)}).status_code == 404
