"""AI versus human agreement and the feedback export (Brief sections 11 and 16)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.outreach.services import record_feedback
from tests.factories import (
    make_ai_recommendation,
    make_campaign,
    make_client,
    make_company,
    make_human_decision,
)
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def world():
    client = make_client()
    campaign = make_campaign(client=client)
    return {"client": client, "campaign": campaign}


def decided(world, ai: str | None, human: str):
    company = make_company(campaign=world["campaign"])
    rec = (
        make_ai_recommendation(company=company, icp_assessment__company=company, status=ai)
        if ai
        else None
    )
    make_human_decision(company=company, decision=human, ai_recommendation=rec)
    return company


def get(world, path, role="manager", **params):
    url = f"/api/v1/campaigns/{world['campaign'].pk}/accounts/{path}/"
    return api_for(role, world["client"]).get(url, params)


def test_the_rate_and_the_table_are_counted_on_seeded_decisions(world) -> None:
    decided(world, "add", "add")
    decided(world, "add", "add")
    decided(world, "add", "skip")
    decided(world, "skip", "hold")
    decided(world, None, "add")
    data = get(world, "agreement").json()
    assert (data["compared"], data["agreed"], data["disagreed"]) == (4, 2, 2)
    assert data["without_ai"] == 1
    assert data["rate"] == 0.5
    cell = {(c["ai"], c["human"]): c["count"] for c in data["matrix"]}
    assert cell[("add", "add")] == 2
    assert cell[("add", "skip")] == 1
    assert cell[("skip", "hold")] == 1
    assert len(data["matrix"]) == 9


def test_only_the_latest_decision_of_a_company_counts(world) -> None:
    company = decided(world, "add", "skip")
    rec = company.ai_recommendations.first()
    make_human_decision(company=company, decision="add", ai_recommendation=rec)
    data = get(world, "agreement").json()
    assert (data["compared"], data["agreed"]) == (1, 1)


def test_the_date_range_limits_the_decisions(world) -> None:
    decided(world, "add", "add")
    tomorrow = (timezone.now() + timedelta(days=2)).date().isoformat()
    data = get(world, "agreement", start=tomorrow).json()
    assert data["compared"] == 0
    assert data["rate"] is None


def test_feedback_is_counted_by_kind_and_exported_with_the_prompt_version(world) -> None:
    company = decided(world, "add", "add")
    record_feedback(company, "wrong_buyer", note="=HYPERLINK(1)")
    record_feedback(company, "wrong_buyer")
    record_feedback(company, "not_b2b")
    data = get(world, "agreement").json()
    assert {f["kind"]: f["count"] for f in data["feedback"]} == {"wrong_buyer": 2, "not_b2b": 1}
    csv_text = get(world, "feedback-export").content.decode()
    assert "rec-v1" in csv_text
    assert "'=HYPERLINK(1)" in csv_text
    assert csv_text.count("\n") == 4


def test_the_report_is_for_managers_and_stays_inside_the_client(world) -> None:
    assert get(world, "agreement", role="reviewer").status_code == 403
    assert get(world, "feedback-export", role="viewer").status_code == 403
    other = make_company()
    make_human_decision(company=other, decision="add")
    assert get(world, "agreement").json()["without_ai"] == 0
