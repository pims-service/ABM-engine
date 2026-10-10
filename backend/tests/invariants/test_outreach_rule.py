"""No outreach message without the latest human decision being ``add`` (ADR 0007).

Uses the real ``HumanDecision`` model (no decision-lookup stubs).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError

from apps.outreach import services
from apps.outreach.models import Message, MessageStatus
from apps.outreach.services import HumanDecisionRequired
from apps.research import services as research
from tests.factories import make_angle, make_company, make_contact, make_user

T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def company(db):
    return make_company()


@pytest.fixture
def angle(company):
    return make_angle(company=company)


@pytest.fixture
def contact(company):
    return make_contact(company=company)


def decide(company, decision, day=0):
    return research.record_human_decision(
        company, decision, make_user(), decided_at=T0 + timedelta(days=day)
    )


def draft(angle, contact):
    return services.create_message(angle, contact, "linkedin", "en", "Hello there")


def test_no_decision_means_no_message(angle, contact) -> None:
    with pytest.raises(HumanDecisionRequired):
        draft(angle, contact)
    assert not Message.objects.exists()


@pytest.mark.parametrize("choice", ["hold", "skip"])
def test_hold_and_skip_block_messages(company, angle, contact, choice) -> None:
    decide(company, choice)
    with pytest.raises(HumanDecisionRequired):
        draft(angle, contact)
    assert not Message.objects.exists()


def test_add_allows_a_draft_and_its_approval(company, angle, contact) -> None:
    decide(company, "add")
    message = draft(angle, contact)
    assert message.status == MessageStatus.DRAFT
    assert services.approve_message(message, make_user()).status == MessageStatus.APPROVED


def test_the_latest_decision_wins_so_a_later_hold_blocks_new_drafts(
    company, angle, contact
) -> None:
    decide(company, "add", day=0)
    existing = draft(angle, contact)
    decide(company, "hold", day=1)
    with pytest.raises(HumanDecisionRequired):
        draft(angle, contact)
    with pytest.raises(HumanDecisionRequired):
        services.approve_message(existing, make_user())
    with pytest.raises(HumanDecisionRequired):
        services.supersede_message(existing, "edited")
    assert Message.objects.count() == 1
    existing.refresh_from_db()
    assert existing.status == MessageStatus.DRAFT
    decide(company, "add", day=2)  # a new add reopens outreach
    assert draft(angle, contact).pk != existing.pk


def test_an_older_add_cannot_override_a_newer_skip_even_if_recorded_later(
    company, angle, contact
) -> None:
    decide(company, "skip", day=5)
    decide(company, "add", day=1)  # recorded now, but decided earlier
    with pytest.raises(HumanDecisionRequired):
        draft(angle, contact)


def test_another_companys_add_does_not_unlock_this_one(company, angle, contact) -> None:
    decide(make_company(campaign=company.campaign), "add")
    with pytest.raises(HumanDecisionRequired):
        draft(angle, contact)


def test_exporting_needs_an_approved_message_and_approval_needs_the_decision(
    company, angle, contact
) -> None:
    decide(company, "add")
    message = draft(angle, contact)
    with pytest.raises(ValidationError):
        services.mark_exported(message)  # still a draft
    services.approve_message(message, make_user())
    assert services.mark_exported(message).status == MessageStatus.EXPORTED


def test_messages_are_only_built_in_the_service_layer() -> None:
    """A static guard: a second code path that constructs ``Message(...)`` would skip the rule."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "apps"
    offenders = []
    for path in root.rglob("*.py"):
        if "migrations" in path.parts or path.name == "models.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "Message(" in text.replace("getMessage(", "") and path.name != "services.py":
            offenders.append(str(path.relative_to(root)))
        if "Message.objects.create" in text or "Message.objects.bulk_create" in text:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"create messages through outreach.services only: {offenders}"
