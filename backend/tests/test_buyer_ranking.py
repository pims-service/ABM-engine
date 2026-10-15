"""The explainable ranking of the people at a company (Brief sections 8 and 15)."""

from __future__ import annotations

from typing import Any

import pytest

from apps.companies.models import Contact
from apps.research.buyers import DEFAULT_LARGE_TITLES, rank_people
from tests.factories import make_campaign, make_client, make_company, make_contact, make_research
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]

PREFERRED = ["VP BD", "Head of BD", "Commercial Director", "Sales Director", "CEO", "Founder"]


def rank(people: list[tuple[str, str]], employees: int | None, preferred=None, departments=()):
    result = rank_people(
        [(name, name, title) for name, title in people],
        preferred=PREFERRED if preferred is None else preferred,
        departments=list(departments),
        employees=employees,
    )
    return {p.name: p for p in result.people}, result


# ------------------------------------------------------------------ the rules (pure)


@pytest.mark.unit
def test_a_large_company_ranks_by_the_campaign_order_not_the_ceo() -> None:
    people, result = rank(
        [("Ceo", "CEO"), ("Bd", "Head of BD"), ("Sales", "Sales Director")], employees=300
    )
    assert [p.name for p in result.people] == ["Bd", "Sales", "Ceo"]
    assert people["Bd"].suggested_role == "primary"
    assert people["Sales"].suggested_role == "secondary"
    assert people["Ceo"].suggested_role == "none"
    assert any("preferred title #2" in r for r in people["Bd"].reasons)


@pytest.mark.unit
def test_a_small_company_puts_the_founder_and_ceo_first() -> None:
    people, result = rank(
        [("Bd", "Head of BD"), ("Ceo", "CEO"), ("Fo", "Co-Founder")], employees=12
    )
    assert [p.name for p in result.people] == ["Fo", "Ceo", "Bd"]
    assert people["Fo"].suggested_role == "primary"
    assert people["Ceo"].suggested_role == "secondary"
    assert any("Small company" in r for r in people["Fo"].reasons)


@pytest.mark.unit
def test_an_unknown_size_uses_the_campaign_order_and_says_so() -> None:
    _, result = rank([("Ceo", "CEO"), ("Bd", "VP BD")], employees=None)
    assert [p.name for p in result.people] == ["Bd", "Ceo"]
    assert "size is unknown" in result.note


@pytest.mark.unit
def test_short_forms_and_long_forms_of_a_title_match() -> None:
    people, _ = rank(
        [("A", "Vice President Business Development"), ("B", "Chief Executive Officer")],
        employees=None,
    )
    assert people["A"].rank == 1
    assert any("is your preferred title #1" in r for r in people["A"].reasons)
    assert any("is your preferred title #5" in r for r in people["B"].reasons)


@pytest.mark.unit
def test_a_title_that_contains_a_preferred_one_ranks_below_an_exact_match() -> None:
    people, _ = rank(
        [("Exact", "Sales Director"), ("Long", "Regional Sales Director MENA")], employees=None
    )
    assert people["Exact"].rank == 1
    assert any("contains" in r for r in people["Long"].reasons)


@pytest.mark.unit
def test_nobody_is_suggested_when_no_title_matches() -> None:
    people, _ = rank([("X", "Office Manager"), ("Y", "Accountant")], employees=300)
    assert {p.suggested_role for p in people.values()} == {"none"}
    assert "matches no preferred title" in people["X"].reasons[0]


@pytest.mark.unit
def test_target_departments_break_ties_and_are_explained() -> None:
    people, _ = rank(
        [("A", "Regional Manager Sales"), ("B", "Regional Manager Finance")],
        employees=None,
        preferred=[],
        departments=["Sales"],
    )
    assert people["A"].rank == 1
    assert any("target department" in r for r in people["A"].reasons)


@pytest.mark.unit
def test_without_preferred_titles_a_large_company_uses_the_briefs_order() -> None:
    assert DEFAULT_LARGE_TITLES[0] == "VP BD"
    people, _ = rank(
        [("Ceo", "CEO"), ("Cco", "Chief Commercial Officer"), ("Vp", "VP Business Development")],
        employees=500,
        preferred=[],
    )
    assert [p.rank for p in (people["Vp"], people["Cco"], people["Ceo"])] == [1, 2, 3]
    assert any("not assumed" in r for r in people["Ceo"].reasons)


# ------------------------------------------------------------------ the API


@pytest.fixture
def world() -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(
        client=client,
        profile__preferred_buyer_titles=PREFERRED,
        profile__target_departments=["Sales", "Business Development"],
    )
    company = make_company(campaign=campaign)
    make_research(company=company, employee_count=300)
    return {"client": client, "company": company, "api": api_for("manager", client)}


def url(world: dict[str, Any], action: str) -> str:
    return f"/api/v1/companies/{world['company'].pk}/{action}/"


def test_the_suggestion_lists_people_with_reasons_and_stores_nothing(world) -> None:
    make_contact(company=world["company"], name="Ceo Person", title="CEO")
    make_contact(company=world["company"], name="Bd Person", title="Head of BD")
    data = api_for("viewer", world["client"]).get(url(world, "buyer-ranking")).json()
    assert [p["name"] for p in data["people"]] == ["Bd Person", "Ceo Person"]
    assert data["people"][0]["suggested_role"] == "primary"
    assert data["people"][0]["reasons"]
    assert "Larger company" in data["note"]
    assert all(c.rank is None for c in Contact.objects.filter(company=world["company"]))


def test_applying_saves_ranks_and_roles_and_demotes_old_holders(world) -> None:
    old = make_contact(company=world["company"], name="Old Primary", title="Office Manager")
    old.role = "primary"
    old.save()
    best = make_contact(company=world["company"], name="Best", title="VP BD")
    second = make_contact(company=world["company"], name="Second", title="Sales Director")
    response = world["api"].post(url(world, "apply-buyer-ranking"), {}, format="json")
    assert response.status_code == 200, response.content
    for contact in (old, best, second):
        contact.refresh_from_db()
    assert (best.rank, best.role) == (1, "primary")
    assert (second.rank, second.role) == (2, "secondary")
    assert old.role == "none"


def test_an_empty_company_gives_an_empty_ranking(world) -> None:
    data = world["api"].get(url(world, "buyer-ranking")).json()
    assert data["people"] == []


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 200), ("admin", 200)]
)
def test_applying_needs_the_edit_level(world, role, status) -> None:
    api = api_for(role, world["client"])
    assert api.post(url(world, "apply-buyer-ranking"), {}, format="json").status_code == status


def test_an_archived_company_cannot_be_changed(world) -> None:
    world["company"].archive()
    assert (
        world["api"].post(url(world, "apply-buyer-ranking"), {}, format="json").status_code == 400
    )
