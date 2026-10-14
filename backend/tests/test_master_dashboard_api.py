"""The master ABM dashboard of a campaign (Brief section 13): rows, filters, counts, bulk queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.companies import analysis
from apps.core.models import Job
from tests.factories import (
    make_ai_recommendation,
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
    make_icp_assessment,
    make_research,
    make_signal,
)
from tests.test_company_api import api_for

pytestmark = [pytest.mark.api, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def queued_tasks(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    queued: list[Any] = []
    monkeypatch.setattr(analysis, "async_task", lambda *a, **k: queued.append(a) or "task-1")
    return queued


def rows_url(campaign: Any) -> str:
    return f"/api/v1/campaigns/{campaign.pk}/accounts/"


def analysed(company: Any, fit: str, rec: str, **research: Any) -> Any:
    snap = make_research(company=company, **research)
    assessment = make_icp_assessment(company=company, company_research=snap, fit=fit)
    make_ai_recommendation(icp_assessment=assessment, status=rec)
    return assessment


@pytest.fixture
def board() -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    strong = make_company(campaign=campaign, name="Atlas", country="SA")
    weak = make_company(campaign=campaign, name="Bravo", country="AE")
    fresh = make_company(campaign=campaign, name="Cedar", country="SA")
    analysed(strong, "strong", "add", industry="Logistics", employee_count=155)
    analysed(weak, "weak", "skip", industry="Retail", employee_count=5000)
    make_signal(
        company=strong,
        type="new_office",
        evidence="Opened an office",
        expires_at=datetime.now(UTC) + timedelta(days=60),
    )
    make_human_decision(company=weak, decision="skip")
    return {
        "client": client,
        "campaign": campaign,
        "strong": strong,
        "weak": weak,
        "fresh": fresh,
        "api": api_for("manager", client),
    }


def names(response: Any) -> list[str]:
    return [row["name"] for row in response.json()["results"]]


def test_rows_show_what_the_research_found(board) -> None:
    rows = {r["name"]: r for r in board["api"].get(rows_url(board["campaign"])).json()["results"]}
    atlas = rows["Atlas"]
    assert atlas["icp_fit"] == "strong"
    assert atlas["trigger"] is True
    assert atlas["trigger_types"] == ["New office"]
    assert atlas["ai_recommendation"] == "add"
    assert atlas["human_decision"] is None
    assert atlas["review"] == "needs_review"
    assert atlas["industry"] == "Logistics"
    assert atlas["employee_count"] == 155
    assert rows["Bravo"]["review"] == "decided"
    assert rows["Bravo"]["human_decision"] == "skip"
    assert rows["Cedar"]["review"] == "not_assessed"
    assert rows["Cedar"]["icp_fit"] is None


def test_the_primary_buyer_is_shown_when_there_is_one(board) -> None:
    make_contact(company=board["strong"], name="Sara Haddad", title="CEO", role="primary")
    rows = {r["name"]: r for r in board["api"].get(rows_url(board["campaign"])).json()["results"]}
    assert rows["Atlas"]["primary_buyer"] == "Sara Haddad, CEO"
    assert rows["Bravo"]["primary_buyer"] is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("icp_fit=strong", ["Atlas"]),
        ("icp_fit=weak", ["Bravo"]),
        ("icp_fit=unassessed", ["Cedar"]),
        ("trigger=yes", ["Atlas"]),
        ("trigger=no", ["Bravo", "Cedar"]),
        ("trigger_type=new_office", ["Atlas"]),
        ("trigger_type=funding", []),
        ("ai=add", ["Atlas"]),
        ("ai=skip", ["Bravo"]),
        ("human=skip", ["Bravo"]),
        ("human=undecided", ["Atlas", "Cedar"]),
        ("review=needs_review", ["Atlas"]),
        ("review=decided", ["Bravo"]),
        ("industry=logis", ["Atlas"]),
        ("country=sa", ["Atlas", "Cedar"]),
        ("size_min=100&size_max=500", ["Atlas"]),
        ("search=brav", ["Bravo"]),
    ],
)
def test_filters(board, query, expected) -> None:
    response = board["api"].get(f"{rows_url(board['campaign'])}?{query}")
    assert response.status_code == 200, response.content
    assert names(response) == expected


def test_an_expired_signal_is_not_a_trigger(board) -> None:
    make_signal(
        company=board["fresh"],
        evidence="Old news",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    response = board["api"].get(f"{rows_url(board['campaign'])}?trigger=yes")
    assert names(response) == ["Atlas"]


def test_buyer_title_filter(board) -> None:
    make_contact(company=board["strong"], name="Sara", title="Managing Director")
    response = board["api"].get(f"{rows_url(board['campaign'])}?buyer_title=managing")
    assert names(response) == ["Atlas"]


def test_ordering_by_icp_fit(board) -> None:
    response = board["api"].get(f"{rows_url(board['campaign'])}?ordering=-icp_fit")
    assert names(response)[:2] == ["Atlas", "Bravo"]


def test_an_invalid_filter_value_is_a_400(board) -> None:
    assert board["api"].get(f"{rows_url(board['campaign'])}?icp_fit=great").status_code == 400


def test_other_campaigns_companies_never_appear(board) -> None:
    other = make_campaign(client=board["client"])
    make_company(campaign=other, name="Elsewhere")
    assert "Elsewhere" not in names(board["api"].get(rows_url(board["campaign"])))


def test_a_campaign_of_another_client_is_404(board) -> None:
    stranger = api_for("admin", make_client())
    assert stranger.get(rows_url(board["campaign"])).status_code == 404


def test_summary_counts(board) -> None:
    data = board["api"].get(f"{rows_url(board['campaign'])}summary/").json()
    assert data["total"] == 3
    assert data["not_assessed"] == 1
    assert (data["strong"], data["medium"], data["weak"]) == (1, 0, 1)
    assert data["trigger_yes"] == 1
    assert data["needs_review"] == 1
    assert data["human_skip"] == 1
    assert data["analyzing"] == 0


def test_bulk_analyze_queues_only_the_unassessed(board, queued_tasks) -> None:
    response = board["api"].post(
        f"{rows_url(board['campaign'])}analyze/", {"scope": "unassessed"}, format="json"
    )
    assert response.status_code == 202, response.content
    assert response.json() == {"requested": 1, "queued": 1, "already_running": 0}
    assert Job.objects.count() == 1
    assert len(queued_tasks) == 1
    # asking again does not queue it twice
    again = board["api"].post(
        f"{rows_url(board['campaign'])}analyze/", {"scope": "unassessed"}, format="json"
    )
    assert again.json() == {"requested": 1, "queued": 0, "already_running": 1}
    summary = board["api"].get(f"{rows_url(board['campaign'])}summary/").json()
    assert summary["analyzing"] == 1


def test_bulk_analyze_by_ids(board) -> None:
    ids = [str(board["strong"].pk), str(board["weak"].pk)]
    response = board["api"].post(
        f"{rows_url(board['campaign'])}analyze/",
        {"scope": "ids", "company_ids": ids},
        format="json",
    )
    assert response.json()["queued"] == 2


def test_bulk_analyze_by_ids_needs_ids(board) -> None:
    response = board["api"].post(
        f"{rows_url(board['campaign'])}analyze/", {"scope": "ids"}, format="json"
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 202), ("admin", 202)]
)
def test_bulk_analyze_needs_edit(board, role, status) -> None:
    response = api_for(role, board["client"]).post(
        f"{rows_url(board['campaign'])}analyze/", {"scope": "unassessed"}, format="json"
    )
    assert response.status_code == status


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager", "admin"])
def test_every_role_can_read_the_dashboard(board, role) -> None:
    api = api_for(role, board["client"])
    assert api.get(rows_url(board["campaign"])).status_code == 200
    assert api.get(f"{rows_url(board['campaign'])}summary/").status_code == 200
