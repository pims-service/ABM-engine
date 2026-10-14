"""People at a company, added by hand (Brief section 8)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.companies.models import Contact
from tests.factories import make_campaign, make_client, make_company, make_contact
from tests.test_company_api import api_for

pytestmark = [pytest.mark.api, pytest.mark.django_db]


def url(company: Any, action: str) -> str:
    return f"/api/v1/companies/{company.pk}/{action}/"


@pytest.fixture
def world() -> tuple[Any, Any]:
    client = make_client()
    return client, make_company(campaign=make_campaign(client=client))


def add(api: Any, company: Any, **body: Any) -> Any:
    return api.post(url(company, "add-contact"), {"name": "Sara Haddad", **body}, format="json")


def test_a_manager_adds_a_person_with_a_manual_source(world) -> None:
    client, company = world
    response = add(
        api_for("manager", client),
        company,
        title="Managing Director",
        email="sara@example.com",
        relevance_reason="Owns the commercial budget",
        role="primary",
    )
    assert response.status_code == 201, response.content
    data = response.json()
    assert (data["name"], data["title"], data["role"]) == (
        "Sara Haddad",
        "Managing Director",
        "primary",
    )
    assert data["email_status"] == "unverified"
    assert data["source"].startswith("Entered by")
    contact = Contact.objects.get(company=company)
    assert contact.data_source.type == "manual"


def test_the_list_is_ordered_primary_secondary_then_rank(world) -> None:
    client, company = world
    make_contact(company=company, name="Zed", rank=2)
    make_contact(company=company, name="Yan", rank=1)
    make_contact(company=company, name="Sec", role="secondary")
    make_contact(company=company, name="Prim", role="primary")
    names = [c["name"] for c in api_for("viewer", client).get(url(company, "contacts")).json()]
    assert names == ["Prim", "Sec", "Yan", "Zed"]


def test_a_new_primary_demotes_the_old_one(world) -> None:
    client, company = world
    api = api_for("manager", client)
    add(api, company, name="First", role="primary")
    add(api, company, name="Second", role="primary")
    roles = {c.name: c.role for c in Contact.objects.filter(company=company)}
    assert roles == {"First": "none", "Second": "primary"}


def test_editing_a_person_and_changing_the_role(world) -> None:
    client, company = world
    contact = make_contact(company=company, name="Old Name", title="CEO")
    api = api_for("manager", client)
    response = api.post(
        url(company, "update-contact"),
        {"contact_id": str(contact.pk), "title": "Founder", "role": "secondary"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["title"] == "Founder"
    assert response.json()["role"] == "secondary"


def test_archiving_hides_a_person(world) -> None:
    client, company = world
    contact = make_contact(company=company)
    api = api_for("manager", client)
    assert (
        api.post(
            url(company, "archive-contact"), {"contact_id": str(contact.pk)}, format="json"
        ).status_code
        == 204
    )
    assert api.get(url(company, "contacts")).json() == []


def test_a_contact_of_another_company_cannot_be_touched(world) -> None:
    client, company = world
    other = make_contact(company=make_company(campaign=company.campaign), name="Other")
    api = api_for("manager", client)
    edit = api.post(
        url(company, "update-contact"),
        {"contact_id": str(other.pk), "title": "Hacked"},
        format="json",
    )
    assert edit.status_code == 400
    assert (
        api.post(
            url(company, "archive-contact"), {"contact_id": str(other.pk)}, format="json"
        ).status_code
        == 400
    )
    other.refresh_from_db()
    assert other.title != "Hacked"
    assert not other.is_archived


def test_a_missing_contact_is_a_400(world) -> None:
    client, company = world
    response = api_for("manager", client).post(
        url(company, "archive-contact"), {"contact_id": str(uuid.uuid4())}, format="json"
    )
    assert response.status_code == 400


def test_validation_errors_are_clear(world) -> None:
    client, company = world
    api = api_for("manager", client)
    assert add(api, company, name="").status_code == 400
    assert add(api, company, email="not-an-email").status_code == 400
    assert add(api, company, role="boss").status_code == 400


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 201), ("admin", 201)]
)
def test_adding_needs_the_edit_level(world, role, status) -> None:
    client, company = world
    assert add(api_for(role, client), company).status_code == status


def test_every_role_can_read_the_people(world) -> None:
    client, company = world
    assert api_for("viewer", client).get(url(company, "contacts")).status_code == 200


def test_the_primary_buyer_shows_on_the_dashboard(world) -> None:
    client, company = world
    api = api_for("manager", client)
    add(api, company, title="CEO", role="primary")
    rows = api.get(f"/api/v1/campaigns/{company.campaign_id}/accounts/").json()["results"]
    assert rows[0]["primary_buyer"] == "Sara Haddad, CEO"
