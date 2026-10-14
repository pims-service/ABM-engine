"""The qualification engine (website -> research, signals, ICP assessment, recommendation).

No network and no real model: the site is a fake transport, the model is a scripted fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from apps.companies.models import CompanyResearch, CompanyStatus
from apps.core.models import Job, JobStatus
from apps.integrations.fetch import fetch_site_pages as real_fetch_site_pages
from apps.integrations.framework.fakes import FakeLLMAdapter
from apps.outreach.models import Activity
from apps.research.engine import AnalysisFailed, run_analysis
from apps.research.engine.freshness import expiry_for
from apps.research.engine.rules import Verdict, apply_rules
from apps.research.models import AIRecommendation, ICPAssessment, Signal
from tests.factories import make_campaign, make_client, make_company
from tests.fetch_helpers import make_fetcher, page, robots

pytestmark = [pytest.mark.django_db]

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

HOME = (
    "<html><head><title>Atlas Freight</title></head><body>"
    "<h1>Atlas Freight</h1><p>Atlas Freight is a Riyadh logistics company serving businesses. "
    "We employ 155 people.</p>"
    '<a href="/news">News</a></body></html>'
)
NEWS = (
    "<html><head><title>News</title></head><body>"
    "<p>On 2026-09-20 Atlas Freight opened a new office in Jeddah to serve western Saudi Arabia."
    "</p></body></html>"
)


class ScriptedLLM(FakeLLMAdapter):
    """Returns the scripted outputs in order, one per call."""

    def __init__(self, outputs: list[Any]) -> None:
        super().__init__({"model": "fake-model"})
        self.script = list(outputs)
        self.index = 0

    def generate_structured(self, prompt: Any, schema: Any, **kwargs: Any) -> Any:
        self.output = self.script[self.index]
        self.index += 1
        return super().generate_structured(prompt, schema, **kwargs)


def facts(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "industry": "Logistics",
        "description": "Logistics company serving businesses.",
        "headquarters": "Riyadh",
        "employee_count": 155,
        "business_model": "Freight services",
        "classification": "b2b",
        "products_services": "Freight",
        "target_customers": "Businesses",
        "signals": [
            {
                "type": "new_office",
                "summary": "Opened a Jeddah office",
                "evidence_quote": "opened a new office in Jeddah",
                "event_date": "2026-09-20",
                "source_url": "https://atlas.example/news",
            }
        ],
    }
    base.update(overrides)
    return base


def verdict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fit": "strong",
        "reasons": ["Saudi B2B logistics company", "155 employees"],
        "concerns": [],
        "recommendation": "add",
        "explanation": "Fits the campaign.",
        "outreach_angle": "Support the sales team with qualified B2B opportunities.",
    }
    base.update(overrides)
    return base


@pytest.fixture
def company():
    client = make_client()
    campaign = make_campaign(
        client=client,
        profile__offer="B2B lead generation",
        profile__countries=["SA"],
        profile__industries=["Logistics"],
        profile__company_size_min=10,
        profile__company_size_max=500,
        profile__business_model="b2b",
        profile__excluded_industries=["Gambling"],
        profile__excluded_company_types=[],
    )
    return make_company(campaign=campaign, name="Atlas Freight", website="https://atlas.example")


def site(extra: dict[str, Any] | None = None) -> Any:
    routes: dict[str, Any] = {
        "atlas.example/": page(HOME),
        "atlas.example/news": page(NEWS),
    }
    routes.update(extra or {})
    fetcher, _, _, _ = make_fetcher(routes)
    return fetcher


def analyse(company, outputs, *, fetcher=None):
    llm = ScriptedLLM(outputs)
    return llm, run_analysis(company, llm, fetcher=fetcher or site(), now=NOW)


# ------------------------------------------------------------------ the happy path


def test_records_research_signals_assessment_and_recommendation(company) -> None:
    llm, outcome = analyse(company, [facts(), verdict()])
    assert llm.index == 2
    research = outcome.research
    assert research.industry == "Logistics"
    assert research.employee_count == 155  # "155 people" is on the page
    assert research.data_source.url == "https://atlas.example/"
    assert outcome.assessment.fit == "strong"
    assert outcome.assessment.reasons == ["Saudi B2B logistics company", "155 employees"]
    assert outcome.recommendation.status == "add"
    assert outcome.recommendation.raw_output["outreach_angle"].startswith("Support the sales")
    [signal] = outcome.signals
    assert signal.type == "new_office"
    assert signal.event_date.isoformat() == "2026-09-20"
    assert signal.data_source.url == "https://atlas.example/news"
    assert "opened a new office in Jeddah" in signal.evidence
    assert signal.expires_at == expiry_for("new_office", signal.event_date)
    assert Signal.objects.filter(company=company).count() == 1
    assert ICPAssessment.objects.filter(company=company).count() == 1
    kinds = set(Activity.objects.filter(company=company).values_list("type", flat=True))
    assert kinds == {"researched", "recommended"}
    assert AIRecommendation.objects.filter(company=company).count() == 1


def test_a_rerun_appends_and_does_not_duplicate_the_same_event(company) -> None:
    analyse(company, [facts(), verdict()])
    analyse(company, [facts(), verdict()])
    assert CompanyResearch.objects.filter(company=company).count() == 2
    assert Signal.objects.filter(company=company).count() == 1


# ------------------------------------------------------------------ nothing is invented


def test_a_signal_whose_quote_is_not_on_the_page_is_dropped(company) -> None:
    fake = facts()
    fake["signals"][0]["evidence_quote"] = "raised a 50 million funding round"
    _, outcome = analyse(company, [fake, verdict()])
    assert outcome.signals == []
    assert outcome.dropped_signals == 1
    assert not Signal.objects.filter(company=company).exists()


def test_a_signal_with_an_unknown_source_page_is_dropped(company) -> None:
    fake = facts()
    fake["signals"][0]["source_url"] = "https://elsewhere.example/news"
    _, outcome = analyse(company, [fake, verdict()])
    assert outcome.dropped_signals == 1


def test_a_future_dated_signal_is_dropped(company) -> None:
    fake = facts()
    fake["signals"][0]["event_date"] = "2027-03-01"
    _, outcome = analyse(company, [fake, verdict()])
    assert outcome.dropped_signals == 1


def test_an_employee_count_the_pages_do_not_state_is_discarded(company) -> None:
    _, outcome = analyse(company, [facts(employee_count=900), verdict()])
    assert outcome.research.employee_count is None


def test_no_signals_is_a_normal_outcome_and_still_gets_a_recommendation(company) -> None:
    _, outcome = analyse(company, [facts(signals=[]), verdict()])
    assert outcome.signals == []
    assert outcome.recommendation.status == "add"  # a trigger is not required


# ------------------------------------------------------------------ campaign rules


def test_an_excluded_industry_forces_weak_and_skip(company) -> None:
    fake = facts(industry="Gambling and betting", signals=[])
    _, outcome = analyse(company, [fake, verdict()])
    assert outcome.assessment.fit == "weak"
    assert outcome.recommendation.status == "skip"
    assert outcome.assessment.concerns[0].startswith("Matches an excluded industry")
    assert "Adjusted by the campaign rules" in outcome.recommendation.explanation


def test_a_b2c_company_in_a_b2b_campaign_is_not_an_add(company) -> None:
    _, outcome = analyse(company, [facts(classification="b2c", signals=[]), verdict()])
    assert outcome.assessment.fit == "weak"
    assert outcome.recommendation.status == "hold"


def test_apply_rules_caps_fit_when_size_is_outside_the_range(company) -> None:
    profile = company.campaign.current_profile
    out = apply_rules(
        Verdict("strong", "add", ["r"], []),
        profile,
        industry="Logistics",
        description=None,
        classification="b2b",
        employee_count=5000,
    )
    assert out.fit == "medium"
    assert out.concerns == ["5000 employees is outside the campaign's size range."]


def test_apply_rules_never_makes_a_weak_fit_an_add(company) -> None:
    out = apply_rules(
        Verdict("weak", "add", ["r"], []),
        company.campaign.current_profile,
        industry=None,
        description=None,
        classification=None,
        employee_count=None,
    )
    assert out.recommendation == "hold"


# ------------------------------------------------------------------ failures


def test_a_site_that_blocks_us_fails_with_a_clear_code(company) -> None:
    fetcher = site({"atlas.example/robots.txt": robots("User-agent: *\nDisallow: /")})
    with pytest.raises(AnalysisFailed) as caught:
        analyse(company, [facts(), verdict()], fetcher=fetcher)
    assert caught.value.code == "fetch_blocked_robots"
    assert not CompanyResearch.objects.filter(company=company).exists()


def test_a_company_without_a_website_fails(company) -> None:
    company.website = ""
    company.save()
    with pytest.raises(AnalysisFailed) as caught:
        analyse(company, [facts(), verdict()])
    assert caught.value.code == "no_website"


def test_invalid_model_output_fails_without_saving_an_assessment(company) -> None:
    with pytest.raises(AnalysisFailed):
        analyse(company, [facts(), {"fit": "amazing"}, {"fit": "amazing"}])
    assert not ICPAssessment.objects.filter(company=company).exists()


# ------------------------------------------------------------------ the worker task


def test_the_task_runs_the_engine_and_marks_the_company_analyzed(
    company, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.companies import analysis, tasks
    from apps.core.jobs import add_items, create_job

    monkeypatch.setattr(
        "apps.research.engine.get_llm_adapter", lambda client_id: ScriptedLLM([facts(), verdict()])
    )

    def fetch_with_fake_site(url: str, **kwargs: Any) -> Any:
        kwargs["fetcher"] = site()
        return real_fetch_site_pages(url, **kwargs)

    monkeypatch.setattr("apps.research.engine.pipeline.fetch_site_pages", fetch_with_fake_site)
    monkeypatch.setattr(analysis, "async_task", lambda *a, **k: "task-1")
    job = create_job(
        tasks.ANALYSIS_JOB_TYPE, client=company.campaign.client, campaign=company.campaign
    )
    add_items(job, [(analysis.SUBJECT_TYPE, company.pk)])
    result = tasks.analyze_company(str(job.pk), str(company.pk))
    job = Job.objects.get(pk=job.pk)
    assert job.status == JobStatus.SUCCEEDED
    assert result is not None
    assert result["fit"] == "strong"
    company.refresh_from_db()
    assert company.status == CompanyStatus.ANALYZED
