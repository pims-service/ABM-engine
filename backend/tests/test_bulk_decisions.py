"""Deciding on many accounts at once (Brief section 11): per-account results, no side effects."""

from __future__ import annotations

import uuid

import pytest

from apps.outreach.models import Activity, ActivityType
from apps.research.models import HumanDecision
from tests.factories import make_campaign, make_client, make_company
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def world():
    client = make_client()
    campaign = make_campaign(client=client)
    companies = [make_company(campaign=campaign) for _ in range(3)]
    return {"client": client, "campaign": campaign, "companies": companies}


def post(world, role, body):
    url = f"/api/v1/campaigns/{world['campaign'].pk}/accounts/decide/"
    return api_for(role, world["client"]).post(url, body, format="json")


def ids(world):
    return [str(c.pk) for c in world["companies"]]


def test_every_account_gets_the_decision_and_a_timeline_entry(world) -> None:
    response = post(world, "reviewer", {"company_ids": ids(world), "decision": "skip", "note": "x"})
    assert response.status_code == 200, response.content
    assert response.json()["saved"] == 3
    assert HumanDecision.objects.filter(decision="skip", note="x").count() == 3
    assert Activity.objects.filter(type=ActivityType.DECIDED).count() == 3


def test_repeating_the_request_changes_nothing(world) -> None:
    post(world, "reviewer", {"company_ids": ids(world), "decision": "add"})
    again = post(world, "reviewer", {"company_ids": ids(world), "decision": "add"}).json()
    assert (again["saved"], again["unchanged"]) == (0, 3)
    assert HumanDecision.objects.count() == 3


def test_one_bad_id_does_not_block_the_others(world) -> None:
    body = {"company_ids": [ids(world)[0], str(uuid.uuid4())], "decision": "hold"}
    data = post(world, "reviewer", body).json()
    assert (data["saved"], data["failed"]) == (1, 1)
    assert data["results"][1]["result"] == "error"


def test_an_archived_account_is_reported_not_decided(world) -> None:
    world["companies"][0].archive()
    data = post(world, "reviewer", {"company_ids": ids(world), "decision": "add"}).json()
    assert (data["saved"], data["failed"]) == (2, 1)


def test_another_clients_account_is_not_found(world) -> None:
    other = make_company()
    data = post(world, "reviewer", {"company_ids": [str(other.pk)], "decision": "add"}).json()
    assert data["failed"] == 1
    assert not HumanDecision.objects.filter(company=other).exists()


@pytest.mark.parametrize(("role", "status"), [("viewer", 403), ("reviewer", 200), ("manager", 200)])
def test_viewers_cannot_decide(world, role, status) -> None:
    assert post(world, role, {"company_ids": ids(world), "decision": "add"}).status_code == status
