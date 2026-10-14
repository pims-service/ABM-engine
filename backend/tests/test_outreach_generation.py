"""Writing, approving, editing and exporting outreach messages (Brief sections 9, 11, 12).

The model is a scripted fake; nothing touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.integrations.llm_connection import NOT_CONFIGURED
from apps.outreach.generate import invented_numbers
from apps.outreach.models import Activity, Message
from tests.factories import (
    make_angle,
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
    make_research,
    make_signal,
)
from tests.test_company_api import api_for
from tests.test_qualification_engine import ScriptedLLM

pytestmark = [pytest.mark.api, pytest.mark.django_db]


def url(company: Any, action: str) -> str:
    return f"/api/v1/companies/{company.pk}/{action}/"


@pytest.fixture
def world() -> dict[str, Any]:
    client = make_client()
    company = make_company(campaign=make_campaign(client=client), name="Atlas Freight")
    make_research(company=company, industry="Logistics", description="Regional freight company.")
    signal = make_signal(
        company=company,
        type="new_office",
        evidence="Opened a new office in Jeddah",
        event_date=(datetime.now(UTC) - timedelta(days=10)).date(),
        expires_at=datetime.now(UTC) + timedelta(days=100),
    )
    angle = make_angle(company=company, signals=[signal], angle="Support the sales team")
    contact = make_contact(company=company, name="Sara Haddad", title="CEO", role="primary")
    return {
        "client": client,
        "company": company,
        "signal": signal,
        "angle": angle,
        "contact": contact,
        "api": api_for("manager", client),
    }


def decide(world: dict[str, Any], decision: str = "add") -> None:
    make_human_decision(company=world["company"], decision=decision)


def drafts(world: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    email = {
        "channel": "email",
        "subject": "Jeddah expansion",
        "body": "Hi Sara, congratulations on the new Jeddah office. We help sales teams.",
        "follow_up": "Hi Sara, any thoughts on my note?",
        "used_signal_ids": [str(world["signal"].pk)],
    }
    linkedin = {
        "channel": "linkedin",
        "subject": None,
        "body": "Hi Sara, saw the Jeddah office news. Open to a short chat?",
        "follow_up": None,
        "used_signal_ids": [str(world["signal"].pk)],
    }
    email.update(overrides)
    return {"drafts": [email, linkedin]}


def use_llm(monkeypatch: pytest.MonkeyPatch, outputs: list[Any]) -> ScriptedLLM:
    llm = ScriptedLLM(outputs)
    monkeypatch.setattr("apps.outreach.messages_api.get_llm_adapter", lambda client_id: llm)
    return llm


def generate(world: dict[str, Any], **body: Any) -> Any:
    payload = {"channels": ["email", "linkedin"], "language": "en", **body}
    return world["api"].post(url(world["company"], "generate-messages"), payload, format="json")


# ------------------------------------------------------------------ generating


def test_writes_drafts_with_their_evidence(world, monkeypatch) -> None:
    decide(world)
    use_llm(monkeypatch, [drafts(world)])
    response = generate(world)
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["rejected"] == []
    kinds = sorted((m["channel"], m["is_followup"]) for m in data["messages"])
    assert kinds == [("email", False), ("email", True), ("linkedin", False)]
    email = next(m for m in data["messages"] if m["channel"] == "email" and not m["is_followup"])
    assert email["status"] == "draft"
    assert email["subject"] == "Jeddah expansion"
    assert email["evidence"][0]["type_label"] == "New office"
    follow = next(m for m in data["messages"] if m["is_followup"])
    assert follow["subject"] == "Re: Jeddah expansion"
    assert Message.objects.filter(company=world["company"]).count() == 3


def test_nothing_is_written_without_a_human_add(world, monkeypatch) -> None:
    llm = use_llm(monkeypatch, [drafts(world)])
    assert generate(world).status_code == 400
    decide(world, "hold")
    response = generate(world)
    assert response.status_code == 400
    assert "ADD" in str(response.json())
    assert llm.index == 0  # the model was never called
    assert not Message.objects.exists()


def test_a_draft_with_an_invented_number_is_refused(world, monkeypatch) -> None:
    decide(world)
    bad = drafts(world, body="Hi Sara, we helped 40 firms like yours. Jeddah office news!")
    use_llm(monkeypatch, [bad])
    response = generate(world)
    assert response.status_code == 201
    assert [m["channel"] for m in response.json()["messages"]] == ["linkedin"]
    assert "email" in response.json()["rejected"][0]
    assert "40" in response.json()["rejected"][0]


def test_a_draft_citing_an_unknown_event_is_refused(world, monkeypatch) -> None:
    decide(world)
    bad = drafts(world, used_signal_ids=["00000000-0000-0000-0000-000000000000"])
    use_llm(monkeypatch, [bad])
    response = generate(world)
    assert response.status_code == 201
    assert "not provided" in response.json()["rejected"][0]


def test_an_overlong_linkedin_message_is_refused(world, monkeypatch) -> None:
    decide(world)
    payload = drafts(world)
    payload["drafts"][1]["body"] = "Hello " * 80
    use_llm(monkeypatch, [payload])
    response = generate(world)
    assert any("LinkedIn" in r for r in response.json()["rejected"])


def test_when_every_draft_is_refused_it_is_an_error(world, monkeypatch) -> None:
    decide(world)
    payload = drafts(world, body="We helped 40 firms.")
    payload["drafts"].pop()
    use_llm(monkeypatch, [payload])
    response = generate(world)
    assert response.status_code == 400
    assert "not usable" in str(response.json())


def test_without_an_angle_or_a_person_there_is_a_clear_error(world, monkeypatch) -> None:
    decide(world)
    use_llm(monkeypatch, [drafts(world)])
    world["contact"].role = "none"
    world["contact"].save()
    assert "primary" in str(generate(world).json())
    other = make_company(campaign=world["company"].campaign, name="NoAngle")
    make_human_decision(company=other, decision="add")
    contact = make_contact(company=other, role="primary")
    response = world["api"].post(
        url(other, "generate-messages"),
        {"channels": ["email"], "language": "en", "contact_id": str(contact.pk)},
        format="json",
    )
    assert response.status_code == 400
    assert "angle" in str(response.json())


def test_without_an_ai_connection_it_says_how_to_fix_it(world) -> None:
    decide(world)
    response = generate(world)
    assert response.status_code == 400
    assert NOT_CONFIGURED in str(response.json())


def test_the_language_must_be_one_of_the_campaigns(world, monkeypatch) -> None:
    decide(world)
    use_llm(monkeypatch, [drafts(world)])
    assert generate(world, language="fr").status_code == 400


def test_writing_again_replaces_the_current_drafts(world, monkeypatch) -> None:
    decide(world)
    use_llm(monkeypatch, [drafts(world), drafts(world, body="Hi Sara, Jeddah office news. Hello!")])
    generate(world)
    generate(world)
    listed = world["api"].get(url(world["company"], "messages")).json()
    assert len(listed) == 3  # still one per slot
    assert Message.objects.filter(company=world["company"]).count() == 6  # history kept


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 201), ("admin", 201)]
)
def test_writing_needs_the_edit_level(world, monkeypatch, role, status) -> None:
    decide(world)
    use_llm(monkeypatch, [drafts(world)])
    api = api_for(role, world["client"])
    response = api.post(
        url(world["company"], "generate-messages"),
        {"channels": ["linkedin"], "language": "en"},
        format="json",
    )
    assert response.status_code == status


# ------------------------------------------------------------------ approve, edit, export


def written(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    decide(world)
    use_llm(monkeypatch, [drafts(world)])
    return generate(world).json()["messages"]


def test_approving_and_exporting(world, monkeypatch) -> None:
    messages = written(world, monkeypatch)
    message = messages[0]
    reviewer = api_for("reviewer", world["client"])
    approved = reviewer.post(
        url(world["company"], "approve-message"), {"message_id": message["id"]}, format="json"
    )
    assert approved.status_code == 200, approved.content
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"]
    exported = reviewer.post(
        url(world["company"], "export-message"), {"message_id": message["id"]}, format="json"
    )
    assert exported.json()["status"] == "exported"
    assert Activity.objects.filter(company=world["company"], type="exported").count() == 1


def test_a_draft_cannot_be_exported_before_approval(world, monkeypatch) -> None:
    message = written(world, monkeypatch)[0]
    response = world["api"].post(
        url(world["company"], "export-message"), {"message_id": message["id"]}, format="json"
    )
    assert response.status_code == 400


def test_a_viewer_cannot_approve(world, monkeypatch) -> None:
    message = written(world, monkeypatch)[0]
    response = api_for("viewer", world["client"]).post(
        url(world["company"], "approve-message"), {"message_id": message["id"]}, format="json"
    )
    assert response.status_code == 403


def test_approval_needs_the_add_decision_to_still_stand(world, monkeypatch) -> None:
    message = written(world, monkeypatch)[0]
    decide(world, "skip")
    response = world["api"].post(
        url(world["company"], "approve-message"), {"message_id": message["id"]}, format="json"
    )
    assert response.status_code == 400


def test_editing_writes_a_new_draft_and_keeps_the_old_one(world, monkeypatch) -> None:
    message = next(m for m in written(world, monkeypatch) if m["channel"] == "linkedin")
    reviewer = api_for("reviewer", world["client"])
    reviewer.post(
        url(world["company"], "approve-message"), {"message_id": message["id"]}, format="json"
    )
    edited = reviewer.post(
        url(world["company"], "edit-message"),
        {"message_id": message["id"], "body": "Hi Sara, short and sweet."},
        format="json",
    )
    assert edited.status_code == 200, edited.content
    assert edited.json()["status"] == "draft"  # needs approval again
    assert edited.json()["body"] == "Hi Sara, short and sweet."
    old = Message.objects.get(pk=message["id"])
    assert old.status == "approved"  # untouched
    current = [m["id"] for m in reviewer.get(url(world["company"], "messages")).json()]
    assert message["id"] not in current
    assert edited.json()["id"] in current


def test_a_message_of_another_company_cannot_be_touched(world, monkeypatch) -> None:
    message = written(world, monkeypatch)[0]
    other = make_company(campaign=world["company"].campaign, name="Other")
    response = world["api"].post(
        url(other, "approve-message"), {"message_id": message["id"]}, format="json"
    )
    assert response.status_code == 400


def test_every_role_can_read_the_messages(world, monkeypatch) -> None:
    written(world, monkeypatch)
    viewer = api_for("viewer", world["client"])
    assert viewer.get(url(world["company"], "messages")).status_code == 200


# ------------------------------------------------------------------ the number check


def test_invented_numbers_ignore_numbers_that_are_in_the_evidence() -> None:
    assert invented_numbers("We saw 155 staff and 3 offices", "155 staff") == {"3"}
    assert invented_numbers("No digits here", "anything 12") == set()
    assert invented_numbers("In 2026 we grew", "Opened on 2026-09-20") == set()  # 2026 is there


def test_arabic_digits_are_compared_as_digits() -> None:
    arabic = "".join(chr(0x660 + int(digit)) for digit in "155")
    assert invented_numbers(f"{arabic} staff", "155 employees") == set()
