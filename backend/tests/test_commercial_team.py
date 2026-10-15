"""Commercial team numbers entered by hand, growth from them, and the engine keeping them."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from apps.companies.models import CompanyResearch
from apps.outreach.models import Activity
from apps.research.engine import run_analysis
from tests.factories import make_campaign, make_client, make_company, make_research
from tests.test_company_api import api_for
from tests.test_qualification_engine import NOW, ScriptedLLM, facts, site, verdict

pytestmark = [pytest.mark.api, pytest.mark.django_db]


def url(company: Any) -> str:
    return f"/api/v1/companies/{company.pk}/commercial-team/"


@pytest.fixture
def world() -> dict[str, Any]:
    client = make_client()
    company = make_company(campaign=make_campaign(client=client), website="https://atlas.example")
    return {"client": client, "company": company, "api": api_for("manager", client)}


def enter(world: dict[str, Any], **body: Any) -> Any:
    payload = {"source_note": "Company page, read by hand", **body}
    return world["api"].post(url(world["company"]), payload, format="json")


def test_the_numbers_become_a_snapshot_with_growth_and_a_source(world) -> None:
    response = enter(
        world,
        employees=120,
        sales=10,
        bd=6,
        marketing=4,
        commercial=2,
        history=[{"months_ago": 12, "employees": 100, "sales": 8}],
    )
    assert response.status_code == 200, response.content
    research = response.json()["research"]
    assert research["employee_count"] == 120
    assert research["sales_headcount"] == 10
    assert research["bd_headcount"] == 6
    assert research["marketing_headcount"] == 4
    assert research["commercial_partnerships_headcount"] == 2
    assert research["headcount_change_12m"] == 20.0
    assert research["headcount_change_3m"] is None  # no data for that window: left empty
    assert research["source"]["name"] == "Company page, read by hand"
    assert CompanyResearch.objects.filter(company=world["company"]).count() == 1
    assert Activity.objects.filter(company=world["company"], type="researched").count() == 1


def test_only_the_numbers_given_are_set_and_the_rest_is_kept(world) -> None:
    make_research(company=world["company"], industry="Logistics", employee_count=250)
    response = enter(world, sales=12)
    research = response.json()["research"]
    assert research["industry"] == "Logistics"  # carried from the earlier snapshot
    assert research["employee_count"] == 250
    assert research["sales_headcount"] == 12
    assert CompanyResearch.objects.filter(company=world["company"]).count() == 2  # history


def test_a_second_entry_keeps_the_departments_it_does_not_mention(world) -> None:
    enter(world, sales=10, bd=5)
    research = enter(world, marketing=3).json()["research"]
    assert (research["sales_headcount"], research["bd_headcount"]) == (10, 5)
    assert research["marketing_headcount"] == 3


def test_a_source_and_at_least_one_number_are_required(world) -> None:
    api = world["api"]
    no_source = api.post(url(world["company"]), {"sales": 3}, format="json")
    no_number = enter(world)
    assert no_source.status_code == 400
    assert no_number.status_code == 400
    assert not CompanyResearch.objects.exists()


def test_the_date_cannot_be_in_the_future(world) -> None:
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    assert enter(world, sales=3, as_of=tomorrow).status_code == 400


def test_nonsense_numbers_are_rejected(world) -> None:
    assert enter(world, sales=-1).status_code == 400
    assert enter(world, history=[{"months_ago": 5, "sales": 3}], sales=3).status_code == 400


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 200), ("admin", 200)]
)
def test_entering_numbers_needs_the_edit_level(world, role, status) -> None:
    api = api_for(role, world["client"])
    response = api.post(url(world["company"]), {"source_note": "x", "sales": 3}, format="json")
    assert response.status_code == status


def test_a_new_analysis_does_not_lose_the_numbers_a_person_entered(world) -> None:
    enter(world, employees=120, sales=10, bd=6)
    llm = ScriptedLLM([facts(employee_count=None, signals=[]), verdict()])
    outcome = run_analysis(world["company"], llm, fetcher=site(), now=NOW)
    research = outcome.research
    assert research.sales_headcount == 10
    assert research.bd_headcount == 6
    assert research.employee_count == 120
    assert research.description  # and the website facts are there too
    assert CompanyResearch.objects.filter(company=world["company"]).count() == 2
