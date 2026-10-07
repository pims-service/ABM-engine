"""ICP fit and trigger are separate (ADR 0008): fit never reads signals, trigger never reads fit."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone

from apps.research import services
from apps.research.models import AIRecommendation, ICPAssessment, Signal
from tests.factories import make_company, make_data_source, make_research

META: dict[str, Any] = {"model_name": "m", "prompt_version": "p1", "schema_version": "1"}
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def company(db):
    company = make_company()
    make_research(company=company)
    return company


def add_signal(company, **kwargs):
    source = make_data_source(client=company.client)
    return services.create_signal(
        company, source, kwargs.pop("type", "funding"), "evidence", date(2026, 5, 1), **kwargs
    )


class QueryLog:
    """Records the SQL a block runs, to prove which tables it touched."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def __call__(self, execute, sql, params, many, context):
        self.sql.append(sql)
        return execute(sql, params, many, context)

    def touched(self, table: str) -> bool:
        return any(table in statement for statement in self.sql)


# ------------------------------------------------------------------ strong fit, no signals


def test_a_strong_fit_company_with_no_signals_is_a_valid_state(company) -> None:
    assessment = services.record_icp_assessment(company, "strong", ["fits the ICP"], **META)
    rec = services.record_ai_recommendation(assessment, "add", "Strong fit, no trigger yet", **META)
    assert assessment.fit == "strong"
    assert rec.status == "add"
    assert not Signal.objects.filter(company=company).exists()
    state = services.company_trigger_state(company)
    assert state.triggered is False
    assert state.label == "No"
    assert state.signals == []
    # reading it back changes nothing: fit is still strong, still no signals
    assert ICPAssessment.objects.latest_for(company).fit == "strong"


def test_trigger_yes_does_not_change_fit_and_weak_fit_can_have_a_trigger(company) -> None:
    weak = services.record_icp_assessment(company, "weak", ["wrong industry"], **META)
    add_signal(company)
    assert services.company_trigger_state(company).label == "Yes"
    assert ICPAssessment.objects.latest_for(company).pk == weak.pk
    assert ICPAssessment.objects.latest_for(company).fit == "weak"


def test_adding_and_retiring_signals_never_rewrites_an_assessment(company) -> None:
    assessment = services.record_icp_assessment(company, "strong", ["fits"], **META)
    before = ICPAssessment.objects.filter(pk=assessment.pk).values().get()
    signal = add_signal(company)
    services.retire_signal(signal, make_data_source(client=company.client), "wrong")
    assert ICPAssessment.objects.filter(pk=assessment.pk).values().get() == before
    assert ICPAssessment.objects.filter(company=company).count() == 1


# ------------------------------------------------------------------ no cross reads


def test_icp_assessment_creation_never_reads_signals(company) -> None:
    add_signal(company)  # a signal exists, and still must not be looked at
    log = QueryLog()
    with connection.execute_wrapper(log):
        assessment = services.record_icp_assessment(company, "medium", ["ok"], **META)
        services.record_ai_recommendation(assessment, "hold", "needs a trigger first", **META)
    assert log.sql, "the wrapper saw no queries"
    assert not log.touched(Signal._meta.db_table)


def test_computing_the_trigger_never_reads_assessments(company) -> None:
    services.record_icp_assessment(company, "strong", ["fits"], **META)
    add_signal(company)
    log = QueryLog()
    with connection.execute_wrapper(log):
        services.company_trigger_state(company)
    assert log.touched(Signal._meta.db_table)
    assert not log.touched(ICPAssessment._meta.db_table)
    assert not log.touched(AIRecommendation._meta.db_table)


def test_the_assessment_table_has_no_way_to_hold_a_signal_or_trigger() -> None:
    names = {f.name for f in ICPAssessment._meta.get_fields()}
    assert not {n for n in names if "signal" in n or "trigger" in n}
    related = {f.related_model for f in ICPAssessment._meta.get_fields() if f.is_relation}
    assert Signal not in related


# ------------------------------------------------------------------ freshness rules


@pytest.mark.parametrize(
    ("expires_offset", "expected"),
    [
        (None, True),  # no freshness rule yet counts as fresh
        (timedelta(days=30), True),
        (timedelta(microseconds=1), True),  # a hair before expiry
        (timedelta(0), False),  # the exact instant of expiry is stale
        (-timedelta(microseconds=1), False),
        (-timedelta(days=30), False),
    ],
)
def test_trigger_boundary_at_expiry(company, expires_offset, expected) -> None:
    expires = None if expires_offset is None else NOW + expires_offset
    add_signal(company, expires_at=expires)
    assert services.company_trigger_state(company, NOW).triggered is expected
    assert bool(Signal.objects.fresh(NOW).filter(company=company)) is expected


def test_a_signal_is_fresh_until_it_expires_then_stale(company) -> None:
    signal = add_signal(company, expires_at=NOW)
    assert signal.is_fresh(NOW - timedelta(seconds=1))
    assert not signal.is_fresh(NOW)
    assert not signal.is_fresh(NOW + timedelta(days=1))


def test_a_superseded_signal_does_not_count_even_if_unexpired(company) -> None:
    old = add_signal(company)
    source = make_data_source(client=company.client)
    services.supersede_signal(old, source, "fixed", date(2026, 5, 2), expires_at=NOW)
    state = services.company_trigger_state(company, NOW - timedelta(days=1))
    assert [s.evidence for s in state.signals] == ["fixed"]  # only the correction counts
    assert services.company_trigger_state(company, NOW).triggered is False


def test_retiring_a_signal_turns_the_trigger_off(company) -> None:
    signal = add_signal(company)
    assert services.company_trigger_state(company).triggered
    services.retire_signal(signal, make_data_source(client=company.client), "wrong signal")
    assert (
        services.company_trigger_state(company, timezone.now() + timedelta(seconds=1)).label == "No"
    )


def test_only_fresh_signals_are_returned_and_stale_ones_are_kept(company) -> None:
    add_signal(company, expires_at=NOW - timedelta(days=1))
    fresh = add_signal(company, expires_at=NOW + timedelta(days=1))
    state = services.company_trigger_state(company, NOW)
    assert [s.pk for s in state.signals] == [fresh.pk]
    assert Signal.objects.filter(company=company).count() == 2


def test_trigger_of_one_company_never_comes_from_another(company) -> None:
    other = make_company(campaign=company.campaign)
    add_signal(other)
    assert services.company_trigger_state(company).triggered is False
    assert services.company_trigger_state(other).triggered is True
