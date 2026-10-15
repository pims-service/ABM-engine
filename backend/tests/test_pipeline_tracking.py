"""Outreach progress (contacted, replied, meeting booked) and the export of approved accounts."""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest

from apps.outreach.services import approve_message
from tests.factories import (
    allow_outreach,
    make_angle,
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
    make_message,
    make_research,
    make_user,
)
from tests.test_company_api import api_for

pytestmark = [pytest.mark.api, pytest.mark.django_db]


def status_url(company: Any) -> str:
    return f"/api/v1/companies/{company.pk}/outreach-status/"


def accounts_url(campaign: Any, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{campaign.pk}/accounts/{suffix}"


@pytest.fixture
def world() -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    approved = make_company(campaign=campaign, name="Atlas")
    held = make_company(campaign=campaign, name="Bravo")
    make_human_decision(company=approved, decision="add")
    make_human_decision(company=held, decision="hold")
    return {
        "client": client,
        "campaign": campaign,
        "approved": approved,
        "held": held,
        "api": api_for("manager", client),
    }


def step(world: Any, stage: str, company: Any = None) -> Any:
    return world["api"].post(
        status_url(company or world["approved"]), {"stage": stage}, format="json"
    )


# ------------------------------------------------------------------ progress


def test_an_added_account_moves_through_outreach(world) -> None:
    assert step(world, "contacted").json() == {"outreach_status": "contacted"}
    assert step(world, "replied").json() == {"outreach_status": "replied"}
    assert step(world, "meeting_booked").json() == {"outreach_status": "meeting_booked"}


def test_the_furthest_step_never_goes_backwards(world) -> None:
    step(world, "replied")
    assert step(world, "contacted").json() == {"outreach_status": "replied"}


def test_only_an_added_account_can_be_contacted(world) -> None:
    response = step(world, "contacted", world["held"])
    assert response.status_code == 400
    assert "ADD" in str(response.json())


def test_an_unknown_step_is_rejected(world) -> None:
    assert step(world, "ghosted").status_code == 400


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 200), ("admin", 200)]
)
def test_recording_progress_needs_the_edit_level(world, role, status) -> None:
    api = api_for(role, world["client"])
    response = api.post(status_url(world["approved"]), {"stage": "contacted"}, format="json")
    assert response.status_code == status


def test_progress_shows_on_the_card_the_rows_and_the_counts(world) -> None:
    step(world, "replied")
    card = world["api"].get(f"/api/v1/companies/{world['approved'].pk}/card/").json()
    assert card["outreach_status"] == "replied"
    rows = {
        r["name"]: r for r in world["api"].get(accounts_url(world["campaign"])).json()["results"]
    }
    assert rows["Atlas"]["outreach_status"] == "replied"
    assert rows["Bravo"]["outreach_status"] == "none"
    summary = world["api"].get(accounts_url(world["campaign"], "summary/")).json()
    assert (summary["contacted"], summary["replied"], summary["meetings"]) == (1, 1, 0)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("outreach=none", ["Bravo", "Cedar"]),
        ("outreach=contacted", ["Cedar"]),
        ("outreach=replied", ["Atlas"]),
        ("outreach=meeting_booked", []),
        ("human=add", ["Atlas", "Cedar"]),
    ],
)
def test_outreach_filters(world, query, expected) -> None:
    cedar = make_company(campaign=world["campaign"], name="Cedar")
    make_human_decision(company=cedar, decision="add")
    step(world, "replied")
    step(world, "contacted", cedar)
    response = world["api"].get(f"{accounts_url(world['campaign'])}?{query}")
    assert [r["name"] for r in response.json()["results"]] == expected


# ------------------------------------------------------------------ export


def parsed(response: Any) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))


def test_the_export_has_only_added_accounts_with_their_approved_messages(world) -> None:
    company = world["approved"]
    make_research(company=company, industry="Logistics")
    contact = make_contact(
        company=company, name="Sara Haddad", title="CEO", email="sara@example.com", role="primary"
    )
    angle = make_angle(company=company, angle="Support the sales team")
    linkedin = make_message(
        angle=angle, contact=contact, channel="linkedin", language="en", subject="", body="Hi Sara"
    )
    approve_message(linkedin, make_user(), decision_lookup=allow_outreach)
    make_message(
        angle=angle,
        contact=contact,
        channel="email",
        language="en",
        subject="Hello",
        body="Draft only",
    )
    response = world["api"].get(accounts_url(world["campaign"], "export/"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "approved-accounts.csv" in response["Content-Disposition"]
    [row] = parsed(response)
    assert row["Company"] == "Atlas"
    assert row["Industry"] == "Logistics"
    assert row["Contact name"] == "Sara Haddad"
    assert row["Contact email"] == "sara@example.com"
    assert row["Outreach angle"] == "Support the sales team"
    assert row["LinkedIn message"] == "Hi Sara"
    assert row["Email body"] == ""  # a draft is not exported
    assert row["Message status"] == "approved"


def test_the_export_protects_against_formulas(world) -> None:
    evil = make_company(campaign=world["campaign"], name='=HYPERLINK("http://x")')
    make_human_decision(company=evil, decision="add")
    rows = {
        r["Company"]: r
        for r in parsed(world["api"].get(accounts_url(world["campaign"], "export/")))
    }
    assert '\'=HYPERLINK("http://x")' in rows


def test_the_export_needs_the_edit_level_and_stays_inside_the_client(world) -> None:
    for role in ("viewer", "reviewer"):
        response = api_for(role, world["client"]).get(accounts_url(world["campaign"], "export/"))
        assert response.status_code == 403
    stranger = api_for("admin", make_client())
    assert stranger.get(accounts_url(world["campaign"], "export/")).status_code == 404


def test_an_empty_export_is_just_the_header(world) -> None:
    other = make_campaign(client=world["client"])
    response = world["api"].get(accounts_url(other, "export/"))
    assert response.status_code == 200
    assert parsed(response) == []
    assert response.content.decode().startswith("Company,Website")
