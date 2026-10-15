"""Pushing approved accounts to the CRM (GoHighLevel), end to end with a fake transport."""

from __future__ import annotations

from typing import Any

import pytest

from apps.integrations import services
from apps.integrations.crm_connection import NOT_CONFIGURED
from apps.integrations.models import ProviderCall
from apps.integrations.testing import check_connection
from apps.outreach.models import Activity, Message
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
    make_user,
)
from tests.test_company_api import api_for
from tests.test_gohighlevel_adapter import TOKEN, FakeHighLevel

pytestmark = [pytest.mark.api, pytest.mark.django_db]


@pytest.fixture
def crm(monkeypatch: pytest.MonkeyPatch) -> FakeHighLevel:
    fake = FakeHighLevel(new=True)
    monkeypatch.setattr(
        "apps.integrations.adapters.gohighlevel.adapter.UrllibTransport",
        lambda *args, **kwargs: fake,
    )
    return fake


def connect(client: Any) -> Any:
    return services.create_connection(
        client,
        None,
        category="crm",
        provider="gohighlevel",
        name="HighLevel",
        config={"location_id": "LOC123abc"},
        credentials={"api_key": TOKEN},
    )


def ready_company(campaign: Any, name: str = "Atlas Freight", *, email: str = "sara@example.com"):
    company = make_company(campaign=campaign, name=name)
    make_human_decision(company=company, decision="add")
    contact = make_contact(
        company=company, name="Sara Haddad", title="CEO", email=email, role="primary"
    )
    angle = make_angle(company=company, angle="Support the sales team")
    message = make_message(
        angle=angle, contact=contact, channel="linkedin", language="en", subject="", body="Hi Sara"
    )
    approve_message(message, make_user(), decision_lookup=allow_outreach)
    return company


@pytest.fixture
def world() -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    return {
        "client": client,
        "campaign": campaign,
        "company": ready_company(campaign),
        "api": api_for("manager", client),
    }


def push(world: dict[str, Any], company: Any = None, **body: Any) -> Any:
    target = company or world["company"]
    return world["api"].post(f"/api/v1/companies/{target.pk}/push-to-crm/", body, format="json")


def test_the_approved_account_lands_in_the_crm_with_a_tag_and_a_note(world, crm) -> None:
    connect(world["client"])
    response = push(world)
    assert response.status_code == 200, response.content
    assert response.json() == {"action": "created", "remote_id": "cid1", "exported_messages": 1}
    assert crm.paths() == ["/contacts/upsert", "/contacts/cid1/tags", "/contacts/cid1/notes"]
    assert crm.body("/contacts/upsert")["email"] == "sara@example.com"
    note = crm.body("/notes")["body"]
    assert "Angle: Support the sales team" in note
    assert "LinkedIn: Hi Sara" in note
    assert "Atlas Freight" in note


def test_the_messages_become_exported_and_the_push_is_logged(world, crm) -> None:
    connect(world["client"])
    push(world)
    assert Message.objects.get(company=world["company"]).status == "exported"
    assert Activity.objects.filter(company=world["company"], type="exported").count() == 1
    call = ProviderCall.objects.get(operation="push_contact")
    assert (call.category, call.provider, call.status) == ("crm", "gohighlevel", "ok")


def test_pushing_again_updates_without_a_second_note(world, crm) -> None:
    connect(world["client"])
    push(world)
    crm.new = False
    crm.requests.clear()
    again = push(world)
    assert again.json()["action"] == "updated"
    assert again.json()["exported_messages"] == 0
    assert crm.paths() == ["/contacts/upsert", "/contacts/cid1/tags"]


def test_the_card_knows_a_crm_is_connected_and_when_it_was_pushed(world, crm) -> None:
    card_url = f"/api/v1/companies/{world['company'].pk}/card/"
    assert world["api"].get(card_url).json()["crm_provider"] is None
    connect(world["client"])
    before = world["api"].get(card_url).json()
    assert before["crm_provider"] == "gohighlevel"
    assert before["exported_at"] is None
    push(world)
    assert world["api"].get(card_url).json()["exported_at"]


def test_without_a_crm_connection_it_says_how_to_connect_one(world) -> None:
    response = push(world)
    assert response.status_code == 400
    assert NOT_CONFIGURED in str(response.json())


def test_an_account_that_is_not_ready_says_what_is_missing(world, crm) -> None:
    connect(world["client"])
    campaign = world["campaign"]
    held = make_company(campaign=campaign, name="Held")
    make_human_decision(company=held, decision="hold")
    assert "ADD" in str(push(world, held).json())
    no_person = make_company(campaign=campaign, name="NoPerson")
    make_human_decision(company=no_person, decision="add")
    assert "primary" in str(push(world, no_person).json())
    no_email = ready_company(campaign, "NoEmail", email="")
    assert "email" in str(push(world, no_email).json())
    unapproved = make_company(campaign=campaign, name="Unapproved")
    make_human_decision(company=unapproved, decision="add")
    make_contact(company=unapproved, email="x@example.com", role="primary")
    assert "Approve" in str(push(world, unapproved).json())
    assert crm.requests == []  # nothing was sent for any of them


def test_a_rejected_token_is_reported_without_the_token(world, crm) -> None:
    connect(world["client"])
    crm.status = 401
    response = push(world)
    assert response.status_code == 400
    assert "refused" in str(response.json())
    assert TOKEN not in response.content.decode()
    assert Message.objects.get(company=world["company"]).status == "approved"  # not exported


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 200), ("admin", 200)]
)
def test_pushing_needs_the_edit_level(world, crm, role, status) -> None:
    connect(world["client"])
    api = api_for(role, world["client"])
    response = api.post(f"/api/v1/companies/{world['company'].pk}/push-to-crm/", {}, format="json")
    assert response.status_code == status


def test_the_bulk_push_sends_the_ready_ones_and_lists_the_rest(world, crm) -> None:
    connect(world["client"])
    campaign = world["campaign"]
    ready_company(campaign, "Second", email="two@example.com")
    stuck = make_company(campaign=campaign, name="Stuck")
    make_human_decision(company=stuck, decision="add")
    url = f"/api/v1/campaigns/{campaign.pk}/accounts/push-to-crm/"
    response = world["api"].post(url, {}, format="json")
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["pushed"] == 2
    assert [s["company"] for s in data["skipped"]] == ["Stuck"]
    assert "primary" in data["skipped"][0]["reason"]


def test_the_bulk_push_without_a_crm_is_a_clear_error(world) -> None:
    url = f"/api/v1/campaigns/{world['campaign'].pk}/accounts/push-to-crm/"
    response = world["api"].post(url, {}, format="json")
    assert response.status_code == 400
    assert NOT_CONFIGURED in str(response.json())


def test_testing_the_crm_connection_from_settings(world, crm) -> None:
    connection = connect(world["client"])
    outcome = check_connection(connection)
    assert outcome.result == "ok"
    crm.status = 401
    assert check_connection(connection).result == "failed"
