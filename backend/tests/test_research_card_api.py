"""GET /companies/{id}/card/ and POST /companies/{id}/decision/ (Brief sections 10 and 11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.research.models import HumanDecision
from tests.factories import (
    make_ai_recommendation,
    make_campaign,
    make_client,
    make_company,
    make_icp_assessment,
    make_research,
    make_signal,
)
from tests.test_company_api import api_for

pytestmark = [pytest.mark.api, pytest.mark.django_db]


def card_url(company: Any) -> str:
    return f"/api/v1/companies/{company.pk}/card/"


def decision_url(company: Any) -> str:
    return f"/api/v1/companies/{company.pk}/decision/"


@pytest.fixture
def world() -> tuple[Any, Any]:
    client = make_client()
    return client, make_company(campaign=make_campaign(client=client))


def test_an_unresearched_company_has_an_empty_card(world) -> None:
    client, company = world
    data = api_for("viewer", client).get(card_url(company)).json()
    assert data["company"]["name"] == company.name
    assert data["research"] is None
    assert data["icp"] is None
    assert data["recommendation"] is None
    assert data["decision"] is None
    assert data["trigger"] == {"triggered": False, "signals": []}
    assert any("people-data provider" in gap for gap in data["gaps"])


def test_a_researched_company_shows_facts_signals_fit_and_recommendation(world) -> None:
    client, company = world
    research = make_research(company=company, industry="Logistics", employee_count=155)
    make_signal(
        company=company,
        type="new_office",
        evidence="Opened an office",
        event_date=(datetime.now(UTC) - timedelta(days=10)).date(),
        expires_at=datetime.now(UTC) + timedelta(days=100),
    )
    assessment = make_icp_assessment(
        company=company, company_research=research, fit="strong", reasons=["Good fit"]
    )
    make_ai_recommendation(
        icp_assessment=assessment,
        status="add",
        raw_output={"outreach_angle": "Support the BD team"},
    )
    data = api_for("viewer", client).get(card_url(company)).json()
    assert data["research"]["industry"] == "Logistics"
    assert data["research"]["employee_count"] == 155
    assert data["research"]["source"]["name"]
    assert data["trigger"]["triggered"] is True
    assert data["trigger"]["signals"][0]["type_label"] == "New office"
    assert data["icp"]["fit"] == "strong"
    assert data["icp"]["reasons"] == ["Good fit"]
    assert data["recommendation"]["status"] == "add"
    assert data["recommendation"]["outreach_angle"] == "Support the BD team"


def test_an_expired_signal_does_not_make_a_trigger(world) -> None:
    client, company = world
    make_signal(
        company=company,
        evidence="Old news",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    data = api_for("viewer", client).get(card_url(company)).json()
    assert data["trigger"]["triggered"] is False


@pytest.mark.parametrize(
    ("role", "status"),
    [("viewer", 403), ("reviewer", 200), ("manager", 200), ("admin", 200)],
)
def test_deciding_needs_the_reviewer_level(world, role, status) -> None:
    client, company = world
    response = api_for(role, client).post(decision_url(company), {"decision": "add"}, format="json")
    assert response.status_code == status, response.content
    assert HumanDecision.objects.filter(company=company).exists() == (status == 200)


def test_the_decision_is_stored_apart_from_the_recommendation(world) -> None:
    client, company = world
    research = make_research(company=company)
    assessment = make_icp_assessment(company=company, company_research=research)
    recommendation = make_ai_recommendation(icp_assessment=assessment, status="add")
    api = api_for("reviewer", client)
    data = api.post(
        decision_url(company), {"decision": "skip", "note": "Too small"}, format="json"
    ).json()
    assert data["decision"]["decision"] == "skip"
    assert data["decision"]["note"] == "Too small"
    assert data["decision"]["agrees_with_ai"] is False
    assert data["recommendation"]["status"] == "add"  # never edited
    row = HumanDecision.objects.get(company=company)
    assert row.ai_recommendation_id == recommendation.pk


def test_changing_your_mind_adds_a_new_decision_and_the_latest_wins(world) -> None:
    client, company = world
    api = api_for("reviewer", client)
    api.post(decision_url(company), {"decision": "hold"}, format="json")
    data = api.post(decision_url(company), {"decision": "add"}, format="json").json()
    assert HumanDecision.objects.filter(company=company).count() == 2
    assert data["decision"]["decision"] == "add"
    assert data["decision"]["agrees_with_ai"] is None  # no recommendation to compare with


def test_an_invalid_decision_is_rejected(world) -> None:
    client, company = world
    response = api_for("reviewer", client).post(
        decision_url(company), {"decision": "maybe"}, format="json"
    )
    assert response.status_code == 400
    assert not HumanDecision.objects.exists()


def test_an_archived_company_takes_no_decision(world) -> None:
    client, company = world
    company.archive()
    response = api_for("reviewer", client).post(
        decision_url(company), {"decision": "add"}, format="json"
    )
    assert response.status_code == 400


# ------------------------------------------------------------------ activity and feedback


def activity_url(company: Any) -> str:
    return f"/api/v1/companies/{company.pk}/activity/"


def feedback_url(company: Any) -> str:
    return f"/api/v1/companies/{company.pk}/feedback/"


def test_a_decision_appears_on_the_timeline(world) -> None:
    client, company = world
    api = api_for("reviewer", client)
    api.post(decision_url(company), {"decision": "skip", "note": "Too small"}, format="json")
    items = api.get(activity_url(company)).json()
    assert len(items) == 1
    assert items[0]["type"] == "decided"
    assert items[0]["summary"] == "Decided SKIP"
    assert items[0]["detail"] == "Too small"
    assert items[0]["actor"]


def test_the_timeline_is_newest_first_and_marks_disagreement(world) -> None:
    client, company = world
    research = make_research(company=company)
    assessment = make_icp_assessment(company=company, company_research=research)
    make_ai_recommendation(icp_assessment=assessment, status="add")
    api = api_for("reviewer", client)
    api.post(decision_url(company), {"decision": "hold"}, format="json")
    api.post(decision_url(company), {"decision": "add"}, format="json")
    summaries = [i["summary"] for i in api.get(activity_url(company)).json()]
    assert summaries == ["Decided ADD (agrees with the AI)", "Decided HOLD (differs from the AI)"]


def test_feedback_is_stored_on_the_timeline_and_changes_nothing_else(world) -> None:
    client, company = world
    api = api_for("reviewer", client)
    response = api.post(
        feedback_url(company), {"kind": "not_b2b", "note": "Sells to consumers"}, format="json"
    )
    assert response.status_code == 201, response.content
    assert response.json()["summary"] == "Feedback: Company is not B2B"
    assert response.json()["detail"] == "Sells to consumers"
    assert not HumanDecision.objects.exists()


def test_feedback_can_point_at_one_signal_of_the_company(world) -> None:
    client, company = world
    signal = make_signal(company=company, evidence="Opened an office")
    response = api_for("reviewer", client).post(
        feedback_url(company),
        {"kind": "not_a_trigger", "target_type": "signal", "target_id": str(signal.pk)},
        format="json",
    )
    assert response.status_code == 201, response.content


def test_feedback_cannot_point_at_another_companys_signal(world) -> None:
    client, company = world
    other = make_signal(company=make_company(campaign=company.campaign))
    response = api_for("reviewer", client).post(
        feedback_url(company),
        {"kind": "not_a_trigger", "target_type": "signal", "target_id": str(other.pk)},
        format="json",
    )
    assert response.status_code == 400


def test_other_feedback_needs_a_note_and_unknown_kinds_are_rejected(world) -> None:
    client, company = world
    api = api_for("reviewer", client)
    assert api.post(feedback_url(company), {"kind": "other"}, format="json").status_code == 400
    assert api.post(feedback_url(company), {"kind": "nonsense"}, format="json").status_code == 400


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 201), ("manager", 201), ("admin", 201)]
)
def test_feedback_needs_the_reviewer_level(world, role, status) -> None:
    client, company = world
    response = api_for(role, client).post(feedback_url(company), {"kind": "not_b2b"}, format="json")
    assert response.status_code == status


def test_every_role_can_read_the_timeline(world) -> None:
    client, company = world
    assert api_for("viewer", client).get(activity_url(company)).status_code == 200
