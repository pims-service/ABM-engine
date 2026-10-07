"""History behaviour: creating again adds a row and never touches older ones (ADR 0007).

One table of "kinds" drives three checks for each: a second row is added and the first is
byte-identical, the latest query returns the newest, and a failed update leaves data unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from apps.companies.models import CompanyResearch
from apps.core.base import ImmutableRecordError
from apps.outreach import services as outreach
from apps.outreach.models import Activity, Message, OutreachAngle
from apps.research import services as research
from apps.research.models import AIRecommendation, HumanDecision, ICPAssessment, Signal
from tests.factories import (
    make_company,
    make_contact,
    make_data_source,
    make_research,
    make_user,
)
from tests.invariants.test_model_rules import concrete_values, editable_column

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
META: dict[str, Any] = {"model_name": "m", "prompt_version": "p1", "schema_version": "1"}


def day(i: int) -> datetime:
    return BASE + timedelta(days=i)


@contextmanager
def clock(moment: datetime) -> Iterator[None]:
    """Freeze ``timezone.now`` so auto_now_add columns order deterministically."""
    with patch("django.utils.timezone.now", return_value=moment):
        yield


@dataclass
class Kind:
    name: str
    model: Any
    make: Callable[[Any, int], Any]  # (company, i) -> row; a larger i is later in time
    latest: Callable[[Any], Any]  # company -> the newest row


def _research(company, i):
    return make_research(company=company, researched_at=day(i), industry=f"industry {i}")


def _assessment(company, i):
    if not CompanyResearch.objects.filter(company=company).exists():
        make_research(company=company)
    return research.record_icp_assessment(
        company, "strong", [f"reason {i}"], created_at=day(i), **META
    )


def _recommendation(company, i):
    assessment = _assessment(company, i)
    return research.record_ai_recommendation(
        assessment, "add", f"why {i}", created_at=day(i), **META
    )


def _decision(company, i):
    return research.record_human_decision(
        company, "add" if i % 2 == 0 else "hold", make_user(), decided_at=day(i)
    )


def _signal(company, i):
    source = make_data_source(client=company.client)
    return research.create_signal(
        company, source, "funding", f"round {i}", date(2026, 1, 1) + timedelta(days=i)
    )


def _angle(company, i):
    with clock(day(i)):
        return outreach.create_angle(company, f"angle {i}", f"because {i}")


def _message(company, i):
    angle = OutreachAngle.objects.filter(company=company).first() or _angle(company, 0)
    contact = make_contact(company=company)
    with clock(day(i)):
        return outreach.create_message(
            angle, contact, "linkedin", "en", f"hello {i}", decision_lookup=lambda c: "add"
        )


def _activity(company, i):
    return outreach.record_activity(company, "researched", created_at=day(i), n=i)


KINDS = [
    Kind(
        "research",
        CompanyResearch,
        _research,
        lambda c: CompanyResearch.objects.latest_for(c),
    ),
    Kind("assessment", ICPAssessment, _assessment, lambda c: ICPAssessment.objects.latest_for(c)),
    Kind(
        "recommendation",
        AIRecommendation,
        _recommendation,
        lambda c: AIRecommendation.objects.latest_for(c),
    ),
    Kind("decision", HumanDecision, _decision, lambda c: HumanDecision.objects.latest_for(c)),
    Kind(
        "signal",
        Signal,
        _signal,
        lambda c: Signal.objects.for_company(c).latest_first().first(),
    ),
    Kind("angle", OutreachAngle, _angle, lambda c: OutreachAngle.objects.latest_for(c)),
    Kind(
        "message",
        Message,
        _message,
        lambda c: Message.objects.for_company(c).latest_first().first(),
    ),
    Kind("activity", Activity, _activity, lambda c: Activity.objects.timeline(c).first()),
]
IDS = [k.name for k in KINDS]


@pytest.fixture
def company(db):
    return make_company()


@pytest.mark.parametrize("kind", KINDS, ids=IDS)
def test_creating_again_adds_a_row_and_leaves_older_rows_untouched(kind, company) -> None:
    first = kind.make(company, 1)
    snapshot = concrete_values(first)
    second = kind.make(company, 2)
    assert first.pk != second.pk
    assert kind.model.objects.filter(company=company).count() >= 2
    assert concrete_values(first) == snapshot, "the older row changed when a newer one was added"
    # and a third one still leaves both alone
    second_snapshot = concrete_values(second)
    kind.make(company, 3)
    assert concrete_values(first) == snapshot
    assert concrete_values(second) == second_snapshot


@pytest.mark.parametrize("kind", KINDS, ids=IDS)
def test_latest_returns_the_newest_row_whatever_the_insert_order(kind, company) -> None:
    kind.make(company, 5)
    newest = kind.make(company, 9)  # inserted second, and newest
    assert kind.latest(company).pk == newest.pk
    kind.make(company, 2)  # an older one inserted last must not become current
    assert kind.latest(company).pk == newest.pk


@pytest.mark.parametrize("kind", KINDS, ids=IDS)
def test_a_failed_update_leaves_the_data_unchanged(kind, company) -> None:
    row = kind.make(company, 1)
    before = concrete_values(row)
    column = editable_column(kind.model)
    if kind.model is Message:
        row.body = "rewritten"
        with pytest.raises(ImmutableRecordError):
            row.save()
    else:
        with pytest.raises(ImmutableRecordError):
            row.save()
    with pytest.raises(ImmutableRecordError):
        kind.model.objects.filter(pk=row.pk).update(**{column: before[column]})
    with pytest.raises(ImmutableRecordError):
        kind.model.objects.filter(pk=row.pk).delete()
    assert concrete_values(row) == before
    assert kind.model.objects.filter(company=company).count() == 1


def test_a_corrected_signal_keeps_the_original_row(company) -> None:
    source = make_data_source(client=company.client)
    old = research.create_signal(company, source, "funding", "seed", date(2026, 1, 1))
    before = concrete_values(old)
    new = research.supersede_signal(old, source, "corrected", date(2026, 1, 2))
    assert new.supersedes_id == old.pk
    assert concrete_values(old) == before
    assert list(Signal.objects.current().filter(company=company)) == [new]


def test_an_edited_message_is_a_new_row_pointing_at_the_old_one(company) -> None:
    old = _message(company, 1)
    before = concrete_values(old)
    new = outreach.supersede_message(old, "edited body", decision_lookup=lambda c: "add")
    assert new.supersedes_id == old.pk
    assert concrete_values(old) == before
    assert Message.objects.for_company(company).current().get().pk == new.pk
