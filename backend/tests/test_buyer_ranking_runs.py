"""Stored buyer ranking runs, staleness, bulk re-run (#137) and the AI explanation and tie-break
(#132). The model is a scripted fake: no test uses the network."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.campaigns.services import create_profile_version
from apps.companies.models import Company
from apps.companies.services import archive_company
from apps.integrations.framework import ProviderRateLimited
from apps.integrations.framework.fakes import FakeLLMAdapter
from apps.research.buyer_runs import describe_runs, run_ranking
from apps.research.models import BuyerRankingRun
from tests.factories import make_campaign, make_client, make_company, make_contact, make_research
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]


class ScriptedLLM(FakeLLMAdapter):
    """Returns the scripted outputs in order, one per call."""

    def __init__(self, outputs: list[Any]) -> None:
        super().__init__({"model": "fake-model"})
        self.script = list(outputs)
        self.index = 0

    def generate_structured(self, prompt: Any, schema: Any, **kwargs: Any) -> Any:
        self.output = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1
        return super().generate_structured(prompt, schema, **kwargs)


def build(
    preferred: tuple[str, ...] = ("VP BD", "Head of BD"), employees: int | None = 120
) -> tuple[Any, Any]:
    """A client, a campaign with preferred titles and a company of ``employees`` people."""
    client = make_client()
    campaign = make_campaign(client=client)
    create_profile_version(campaign, {"preferred_buyer_titles": list(preferred)})
    company = make_company(campaign=campaign)
    if employees is not None:
        make_research(company=company, employee_count=employees)
    return client, company


def fresh(company: Any) -> Company:
    return Company.objects.select_related("campaign__current_profile").get(pk=company.pk)


def two_people(company: Any) -> tuple[Any, Any]:
    return (
        make_contact(company=company, name="Sara Haddad", title="VP BD"),
        make_contact(company=company, name="Omar Nasser", title="Head of BD"),
    )


# ------------------------------------------------------------------ runs and staleness


def test_a_run_stores_the_ranking_with_its_reasons() -> None:
    _, company = build()
    sara, _omar = two_people(company)
    result = run_ranking(fresh(company))
    assert result.created is True
    assert result.reason == "created"
    run = result.run
    assert len(run.inputs_hash) == 64
    assert [p["name"] for p in run.people] == ["Sara Haddad", "Omar Nasser"]
    first = run.people[0]
    assert first["contact_id"] == str(sara.pk)
    assert (first["rank"], first["suggested_role"]) == (1, "primary")
    assert first["reasons"]
    assert run.explanation is None


def test_running_again_with_the_same_inputs_makes_nothing() -> None:
    _, company = build()
    two_people(company)
    first = run_ranking(fresh(company))
    again = run_ranking(fresh(company))
    assert again.created is False
    assert again.reason == "unchanged"
    assert again.run.pk == first.run.pk
    assert BuyerRankingRun.objects.filter(company=company).count() == 1


def test_force_makes_a_new_run_and_keeps_the_old_one() -> None:
    _, company = build()
    two_people(company)
    first = run_ranking(fresh(company))
    forced = run_ranking(fresh(company), force=True)
    assert forced.created is True
    assert forced.run.pk != first.run.pk
    assert BuyerRankingRun.objects.filter(company=company).count() == 2


def test_changing_the_buyer_rules_marks_the_ranking_stale_without_deleting_it() -> None:
    _, company = build()
    two_people(company)
    run_ranking(fresh(company))
    assert [r["status"] for r in describe_runs(fresh(company))] == ["current"]
    create_profile_version(company.campaign, {"preferred_buyer_titles": ["Head of BD"]})
    runs = describe_runs(fresh(company))
    assert [r["status"] for r in runs] == ["stale"]
    assert BuyerRankingRun.objects.filter(company=company).count() == 1
    rerun = run_ranking(fresh(company))
    assert rerun.created is True
    assert [r["status"] for r in describe_runs(fresh(company))] == ["current", "superseded"]
    assert BuyerRankingRun.objects.filter(company=company).count() == 2


def test_a_new_person_marks_the_ranking_stale() -> None:
    _, company = build()
    two_people(company)
    run_ranking(fresh(company))
    make_contact(company=company, name="Lina Aziz", title="Commercial Director")
    assert describe_runs(fresh(company))[0]["status"] == "stale"
    assert run_ranking(fresh(company)).created is True


def test_newer_research_with_a_different_size_marks_the_ranking_stale() -> None:
    _, company = build(employees=120)
    two_people(company)
    run_ranking(fresh(company))
    make_research(company=company, employee_count=20)  # now a small company
    assert describe_runs(fresh(company))[0]["status"] == "stale"


def test_a_company_without_people_gets_an_empty_run_once() -> None:
    _, company = build()
    first = run_ranking(fresh(company))
    assert first.run.people == []
    assert run_ranking(fresh(company)).created is False


def test_an_archived_company_is_not_ranked() -> None:
    _, company = build()
    archive_company(company)
    with pytest.raises(ValidationError):
        run_ranking(fresh(company))


# ------------------------------------------------------------------ the endpoints


def url(company: Any, tail: str = "run/") -> str:
    return f"/api/v1/companies/{company.pk}/buyer-ranking/{tail}"


def test_the_run_endpoint_makes_a_run_then_says_nothing_changed() -> None:
    client, company = build()
    two_people(company)
    manager = api_for("manager", client)
    first = manager.post(url(company), {}, format="json")
    assert first.status_code == 201, first.content
    assert first.json()["created"] is True
    assert first.json()["run"]["status"] == "current"
    again = manager.post(url(company), {}, format="json")
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["reason"] == "unchanged"
    forced = manager.post(url(company), {"force": True}, format="json")
    assert forced.status_code == 201


def test_the_runs_endpoint_lists_newest_first_with_statuses() -> None:
    client, company = build()
    two_people(company)
    manager = api_for("manager", client)
    manager.post(url(company), {}, format="json")
    manager.post(url(company), {"force": True}, format="json")
    runs = api_for("viewer", client).get(url(company, "runs/")).json()
    assert [r["status"] for r in runs] == ["current", "superseded"]
    assert runs[0]["people"][0]["name"] == "Sara Haddad"


def test_a_viewer_can_read_the_runs_but_not_make_one() -> None:
    client, company = build()
    viewer = api_for("viewer", client)
    assert viewer.post(url(company), {}, format="json").status_code == 403
    assert viewer.get(url(company, "runs/")).status_code == 200


def test_another_clients_company_is_not_found() -> None:
    _, company = build()
    outsider = api_for("manager", make_client())
    assert outsider.post(url(company), {}, format="json").status_code == 404
    assert outsider.get(url(company, "runs/")).status_code == 404


def test_the_bulk_rerun_reports_each_account_on_its_own() -> None:
    client, company = build()
    two_people(company)
    second = make_company(campaign=company.campaign)
    manager = api_for("manager", client)
    path = f"/api/v1/campaigns/{company.campaign.pk}/accounts/rerun-buyer-ranking/"
    ghost = str(uuid.uuid4())
    body = {"company_ids": [str(company.pk), str(second.pk), ghost]}
    first = manager.post(path, body, format="json")
    assert first.status_code == 200, first.content
    rows = {row["company_id"]: row for row in first.json()}
    assert rows[str(company.pk)]["status"] == "created"
    assert rows[str(second.pk)]["status"] == "created"
    assert rows[ghost]["status"] == "error"
    assert rows[ghost]["run_id"] is None
    again = {r["company_id"]: r for r in manager.post(path, body, format="json").json()}
    assert again[str(company.pk)]["status"] == "unchanged"
    forced = manager.post(path, {**body, "force": True}, format="json").json()
    assert {r["status"] for r in forced if r["company_id"] != ghost} == {"created"}


def test_the_bulk_rerun_needs_the_edit_level_and_a_sane_list() -> None:
    client, company = build()
    path = f"/api/v1/campaigns/{company.campaign.pk}/accounts/rerun-buyer-ranking/"
    assert (
        api_for("viewer", client)
        .post(path, {"company_ids": [str(company.pk)]}, format="json")
        .status_code
        == 403
    )
    manager = api_for("manager", client)
    assert manager.post(path, {"company_ids": []}, format="json").status_code == 400
    assert manager.post(path, {}, format="json").status_code == 400


# ------------------------------------------------------------------ the AI explanation


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a scripted model; the test sets ``model.script`` before it runs."""
    holder = type("Holder", (), {"llm": None})()

    def install(outputs: list[Any]) -> ScriptedLLM:
        holder.llm = ScriptedLLM(outputs)
        monkeypatch.setattr(
            "apps.research.buyer_explain.get_llm_adapter", lambda *a, **k: holder.llm
        )
        return holder.llm  # type: ignore[no-any-return]

    return install


def choice(primary: Any, secondary: Any = None, **extra: Any) -> dict[str, Any]:
    return {
        "primary_contact_id": str(primary),
        "secondary_contact_id": None if secondary is None else str(secondary),
        "rationale": "Matches the first preferred title.",
        "concerns": [],
        **extra,
    }


def test_without_an_ai_connection_the_run_keeps_the_rules_and_says_so() -> None:
    _, company = build()
    two_people(company)
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["source"] == "rules"
    assert "No AI provider" in run.explanation["note"]
    assert run.people[0]["name"] == "Sara Haddad"
    assert run.model_name == ""


def test_the_model_explains_the_rules_choice(model: Any) -> None:
    _, company = build()
    sara, omar = two_people(company)
    llm = model([choice(sara.pk, omar.pk)])
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["source"] == "model"
    assert run.explanation["tie_broken"] is False
    assert run.explanation["rationale"]
    assert (run.model_name, run.prompt_version, run.schema_version) == ("fake-model", "1", "1")
    assert [p["name"] for p in run.people] == ["Sara Haddad", "Omar Nasser"]
    assert llm.index == 1


def test_the_model_breaks_a_tie_between_equally_ranked_people(model: Any) -> None:
    _, company = build(preferred=("VP BD",))
    amal = make_contact(company=company, name="Amal Saleh", title="VP BD")
    basil = make_contact(company=company, name="Basil Omar", title="VP BD")
    third = make_contact(company=company, name="Dina Khan", title="Marketing Manager")
    model([choice(basil.pk, third.pk)])
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["tie_broken"] is True
    assert sorted(run.explanation["tied_contact_ids"]) == sorted([str(amal.pk), str(basil.pk)])
    assert [p["name"] for p in run.people][:2] == ["Basil Omar", "Amal Saleh"]
    assert run.people[0]["suggested_role"] == "primary"
    assert any("AI model" in reason for reason in run.people[0]["reasons"])


def test_a_choice_outside_the_tied_people_is_refused_and_the_rules_stand(model: Any) -> None:
    _, company = build(preferred=("VP BD",))
    make_contact(company=company, name="Amal Saleh", title="VP BD")
    make_contact(company=company, name="Basil Omar", title="VP BD")
    outsider = make_contact(company=company, name="Dina Khan", title="Marketing Manager")
    llm = model([choice(outsider.pk), choice(outsider.pk)])
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["source"] == "rules"
    assert run.people[0]["name"] == "Amal Saleh"
    assert llm.index == 2  # one more try, then the rules


def test_a_person_the_model_invents_is_refused(model: Any) -> None:
    _, company = build()
    two_people(company)
    model([choice(uuid.uuid4()), choice(uuid.uuid4())])
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["source"] == "rules"


def test_a_second_try_can_succeed(model: Any) -> None:
    _, company = build()
    sara, omar = two_people(company)
    llm = model([choice(uuid.uuid4()), choice(sara.pk, omar.pk)])
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["source"] == "model"
    assert llm.index == 2


def test_a_provider_failure_falls_back_to_the_rules(model: Any) -> None:
    _, company = build()
    two_people(company)
    llm = model([{}])
    llm.fail_with = ProviderRateLimited(5)
    run = run_ranking(fresh(company), explain=True).run
    assert run.explanation is not None
    assert run.explanation["source"] == "rules"
    assert run.people[0]["name"] == "Sara Haddad"


def test_asking_for_the_explanation_makes_a_run_only_when_there_is_none(model: Any) -> None:
    _, company = build()
    sara, omar = two_people(company)
    plain = run_ranking(fresh(company))
    assert plain.created is True
    model([choice(sara.pk, omar.pk)])
    explained = run_ranking(fresh(company), explain=True)
    assert explained.created is True  # the plain run has no AI explanation
    again = run_ranking(fresh(company), explain=True)
    assert again.created is False
    assert again.run.pk == explained.run.pk
