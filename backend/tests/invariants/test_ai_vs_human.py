"""AI recommendation and human decision are separate records (ADR 0007, Brief section 11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.core.base import TenantMismatchError
from apps.research import services
from apps.research.models import AIRecommendation, HumanDecision, ICPAssessment
from tests.factories import make_company, make_research, make_user

META: dict[str, Any] = {"model_name": "m", "prompt_version": "p1", "schema_version": "1"}
T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def company(db):
    company = make_company()
    make_research(company=company)
    return company


def recommend(company, status="add", **kwargs):
    assessment = services.record_icp_assessment(company, "strong", ["fits"], **META)
    return services.record_ai_recommendation(assessment, status, "because", **META, **kwargs)


def test_they_live_in_different_tables_with_no_shared_outcome_columns() -> None:
    tables = {m._meta.db_table for m in (ICPAssessment, AIRecommendation, HumanDecision)}
    assert len(tables) == 3
    ai = {f.name for f in AIRecommendation._meta.concrete_fields}
    human = {f.name for f in HumanDecision._meta.concrete_fields}
    assert not {"decision", "decided_by", "decided_at", "note"} & ai
    assert not {"status", "explanation", "raw_output", "model_name", "icp_assessment"} & human


def test_recording_a_decision_never_changes_the_recommendation(company) -> None:
    rec = recommend(company, "add")
    before = AIRecommendation.objects.filter(pk=rec.pk).values().get()
    services.record_human_decision(company, "skip", make_user(), ai_recommendation=rec, note="no")
    assert AIRecommendation.objects.filter(pk=rec.pk).values().get() == before
    assert AIRecommendation.objects.filter(company=company).count() == 1
    assert HumanDecision.objects.get(company=company).ai_recommendation_id == rec.pk


def test_a_new_recommendation_never_changes_a_decision(company) -> None:
    rec = recommend(company, "add")
    decision = services.record_human_decision(company, "hold", make_user(), ai_recommendation=rec)
    before = HumanDecision.objects.filter(pk=decision.pk).values().get()
    recommend(company, "skip")
    assert HumanDecision.objects.filter(pk=decision.pk).values().get() == before
    assert HumanDecision.objects.latest_for(company).decision == "hold"
    assert AIRecommendation.objects.latest_for(company).status == "skip"


def test_changing_ones_mind_adds_a_decision_row(company) -> None:
    user = make_user()
    rec = recommend(company)
    first = services.record_human_decision(
        company, "hold", user, ai_recommendation=rec, decided_at=T0
    )
    second = services.record_human_decision(
        company, "add", user, ai_recommendation=rec, decided_at=T0 + timedelta(days=1)
    )
    assert HumanDecision.objects.filter(company=company).count() == 2
    assert HumanDecision.objects.latest_for(company).pk == second.pk
    assert HumanDecision.objects.get(pk=first.pk).decision == "hold"


def test_a_decision_needs_a_person_and_a_valid_choice(company) -> None:
    with pytest.raises(ValidationError):
        services.record_human_decision(company, "add", None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        services.record_human_decision(company, "maybe", make_user())


def test_agreement_summary_counts_matches_overrides_and_unassisted(company) -> None:
    user = make_user()
    other = make_company(campaign=company.campaign)
    make_research(company=other)
    third = make_company(campaign=company.campaign)
    make_research(company=third)
    services.record_human_decision(
        company, "add", user, ai_recommendation=recommend(company, "add")
    )  # agrees
    services.record_human_decision(
        other, "skip", user, ai_recommendation=recommend(other, "add")
    )  # overrides
    services.record_human_decision(third, "hold", user)  # no AI: left out of the rate
    summary = HumanDecision.objects.agreement()
    assert (summary.compared, summary.agreed, summary.disagreed) == (2, 1, 1)
    assert summary.without_ai == 1
    assert summary.rate == 0.5
    assert summary.pairs == {("add", "add"): 1, ("add", "skip"): 1}
    assert HumanDecision.objects.overrides().get().company_id == other.pk


def test_agreement_with_nothing_compared_has_no_rate(db) -> None:
    summary = HumanDecision.objects.agreement()
    assert summary.compared == 0
    assert summary.rate is None


def test_agreement_can_use_only_each_companys_latest_decision(company) -> None:
    user = make_user()
    rec = recommend(company, "add")
    services.record_human_decision(company, "skip", user, ai_recommendation=rec, decided_at=T0)
    services.record_human_decision(
        company, "add", user, ai_recommendation=rec, decided_at=T0 + timedelta(days=1)
    )
    assert HumanDecision.objects.agreement().compared == 2
    current = HumanDecision.objects.current().agreement()
    assert (current.compared, current.agreed) == (1, 1)


def test_decision_for_another_companys_recommendation_is_refused(company) -> None:
    other = make_company(campaign=company.campaign)
    make_research(company=other)
    rec = recommend(other)

    with pytest.raises(TenantMismatchError):
        services.record_human_decision(company, "add", make_user(), ai_recommendation=rec)
    assert not HumanDecision.objects.exists()


@pytest.mark.parametrize("bad", [{"score": 9}, {"nested": {"Fit_Score": 1}}, {"x": [{"score": 1}]}])
def test_scores_in_model_output_are_refused(company, bad) -> None:
    with pytest.raises(ValidationError):
        services.record_icp_assessment(company, "strong", ["r"], raw_output=bad, **META)
    assessment = services.record_icp_assessment(company, "strong", ["r"], **META)
    with pytest.raises(ValidationError):
        services.record_ai_recommendation(assessment, "add", "why", raw_output=bad, **META)
