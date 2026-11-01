"""The evidence service and the department taxonomy (issues #100 and #96)."""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from apps.companies.models import CompanyResearch, DataSource, DataSourceType
from apps.research import taxonomy
from apps.research.evidence import age_days, company_evidence, record_evidence, signal_sources
from apps.research.models import Signal, SignalType
from apps.research.services import create_signal, supersede_signal
from tests.factories import make_campaign, make_client, make_company, make_data_source

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def client() -> Any:
    return make_client()


# ------------------------------------------------------------------ record_evidence


def test_evidence_is_stored_with_its_source_and_dates(client: Any) -> None:
    when = timezone.now() - timedelta(days=3)
    source = record_evidence(
        client,
        DataSourceType.NEWS,
        "Gulf Business",
        url="https://gulfbusiness.example.com/a",
        observed_at=when,
        evidence_date=date(2026, 9, 1),
    )
    assert (source.name, source.url, source.retrieved_at, source.evidence_date) == (
        "Gulf Business",
        "https://gulfbusiness.example.com/a",
        when,
        date(2026, 9, 1),
    )


@pytest.mark.parametrize("name", ["", "   ", None])
def test_evidence_without_a_source_name_is_refused(client: Any, name: Any) -> None:
    with pytest.raises(ValidationError) as info:
        record_evidence(client, DataSourceType.PROVIDER, name)
    assert "name" in info.value.message_dict
    assert DataSource.objects.count() == 0


def test_evidence_from_the_future_is_refused(client: Any) -> None:
    soon = timezone.now() + timedelta(hours=1)
    with pytest.raises(ValidationError) as info:
        record_evidence(client, DataSourceType.PROVIDER, "Acme data", observed_at=soon)
    assert "retrieved_at" in info.value.message_dict
    with pytest.raises(ValidationError) as info:
        record_evidence(
            client,
            DataSourceType.PROVIDER,
            "Acme data",
            evidence_date=date.today() + timedelta(days=30),
        )
    assert "evidence_date" in info.value.message_dict
    assert DataSource.objects.count() == 0


def test_a_few_minutes_of_clock_skew_is_allowed(client: Any) -> None:
    record_evidence(
        client,
        DataSourceType.PROVIDER,
        "Acme data",
        observed_at=timezone.now() + timedelta(minutes=2),
    )


def test_a_web_source_needs_a_url(client: Any) -> None:
    with pytest.raises(ValidationError) as info:
        record_evidence(client, DataSourceType.WEBSITE, "acme.example.com")
    assert "url" in info.value.message_dict


def test_the_database_will_not_hold_a_web_source_without_a_url(client: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DataSource.objects.create(client=client, type=DataSourceType.NEWS, name="No link", url="")


def test_a_snapshot_and_a_signal_must_point_to_a_source() -> None:
    assert CompanyResearch._meta.get_field("data_source").null is False
    assert Signal._meta.get_field("data_source").null is False


def test_only_the_service_constructs_a_data_source() -> None:
    """The one write path: nothing but ``create_data_source`` builds a ``DataSource``."""
    backend = Path(__file__).resolve().parents[1]
    allowed = {backend / "apps" / "companies" / "services.py"}
    offenders = []
    for path in (backend / "apps").rglob("*.py"):
        if path in allowed or "migrations" in path.parts or path.name == "models.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                callee = node.func
                name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
                if name == "DataSource":
                    offenders.append(f"{path.relative_to(backend)}:{node.lineno}")
    assert not offenders, f"build evidence with record_evidence/create_data_source: {offenders}"


# ------------------------------------------------------------------ freshness


def test_age_in_days() -> None:
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    assert age_days(datetime(2026, 10, 22, 8, tzinfo=UTC), now) == 10
    assert age_days(date(2026, 10, 31), now) == 1
    assert age_days(now + timedelta(days=2), now) == 0  # never negative
    assert age_days(datetime(2026, 10, 30, 12), now) == 2  # a naive time is read as UTC


# ------------------------------------------------------------------ evidence of a company


def test_company_evidence_lists_each_source_once_with_what_it_backs() -> None:
    company = make_company()
    first = make_data_source(client=company.client, name="Provider A")
    second = make_data_source(client=company.client, name="Provider B")
    CompanyResearch.objects.create(company=company, data_source=first, industry="Retail")
    signal = create_signal(company, first, SignalType.FUNDING, "Series A", date(2026, 9, 1))
    supersede_signal(signal, second, "Series A, also reported", date(2026, 9, 1))
    rows = company_evidence(company)
    names = {row["name"]: row for row in rows}
    assert set(names) == {"Provider A", "Provider B"}
    kinds_a = sorted(b["kind"] for b in names["Provider A"]["backs"])
    assert kinds_a == ["research_snapshot", "signal", "signal"]
    assert [b["kind"] for b in names["Provider B"]["backs"]] == ["signal"]
    assert all(isinstance(row["age_days"], int) for row in rows)


def test_a_signal_with_two_reports_keeps_both_sources() -> None:
    company = make_company()
    first = make_data_source(client=company.client, name="Provider A")
    second = make_data_source(client=company.client, name="Provider B")
    original = create_signal(company, first, SignalType.FUNDING, "Series A", date(2026, 9, 1))
    newest = supersede_signal(original, second, "Series A", date(2026, 9, 1))
    assert [s.name for s in signal_sources(newest)] == ["Provider B", "Provider A"]
    assert [s.name for s in signal_sources(original)] == ["Provider A"]


# ------------------------------------------------------------------ taxonomy


@pytest.mark.parametrize(
    ("title", "department", "confidence"),
    [
        ("Sales Manager", "sales", "high"),
        ("Senior Account Executive", "sales", "high"),
        ("Business Development Director", "bd", "high"),
        ("BD Lead", "bd", "high"),
        ("Biz-Dev Associate", "bd", "high"),
        ("Digital Marketing Specialist", "marketing", "high"),
        ("Commercial Director", "commercial", "high"),
        ("Strategic Partnerships Manager", "partnerships", "high"),
        ("Channel Partner Manager", "partnerships", "high"),
        ("مدير مبيعات", "sales", "high"),
        ("مدير المبيعات الإقليمي", "sales", "high"),
        ("مدير تطوير الأعمال", "bd", "high"),
        ("أخصائي تسويق رقمي", "marketing", "high"),
        ("مدير الشراكات", "partnerships", "high"),
        ("مسؤول تجاري", "commercial", "high"),
        # more than one department: flagged, never counted
        ("Sales and Marketing Director", "sales", "low"),
        ("Business Development & Partnerships Lead", "bd", "low"),
        # ambiguous titles: a guess, flagged
        ("Head of Growth", "marketing", "low"),
        ("Customer Success Manager", "sales", "low"),
        ("رئيس النمو", "marketing", "low"),
        # unknown stays unknown, and is never Sales by default
        ("Software Engineer", "unclassified", "none"),
        ("Chief Financial Officer", "unclassified", "none"),
        ("مهندس برمجيات", "unclassified", "none"),
        ("", "unclassified", "none"),
        (None, "unclassified", "none"),
    ],
)
def test_titles_are_classified(title: str | None, department: str, confidence: str) -> None:
    result = taxonomy.classify_title(title)
    assert (result.department, result.confidence) == (department, confidence), title


def test_only_confident_titles_count_toward_a_department() -> None:
    counts = taxonomy.tally(
        [
            "Sales Manager",
            "Sales Executive",
            "Head of Growth",  # low
            "Sales and Marketing Director",  # low
            "Software Engineer",  # none
            None,
        ]
    )
    assert counts["sales"] == 2
    assert counts["marketing"] == 0
    assert counts["unclassified"] == 4


def test_a_word_inside_another_word_does_not_match() -> None:
    # "bd" must not be found inside "abdul", "brand" is a marketing word but "brandy" is not one.
    assert taxonomy.classify_title("Abdul the Engineer").department == "unclassified"
    assert taxonomy.classify_title("Brandy Maker").department == "unclassified"


def test_rules_are_data_and_reload_when_the_file_changes(tmp_path: Path) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps({"version": 1, "departments": {"sales": ["wizard"]}}), encoding="utf-8"
    )
    with override_settings(RESEARCH_DEPARTMENT_RULES_FILE=str(rules)):
        assert taxonomy.classify_title("Head Wizard").department == "sales"
        assert taxonomy.classify_title("Sales Manager").department == "unclassified"
        rules.write_text(
            json.dumps({"version": 1, "departments": {"marketing": ["wizard"]}}),
            encoding="utf-8",
        )
        later = rules.stat().st_mtime + 5
        import os

        os.utime(rules, (later, later))
        assert taxonomy.classify_title("Head Wizard").department == "marketing"


def test_a_rules_file_with_an_unknown_department_is_refused(tmp_path: Path) -> None:
    rules = tmp_path / "bad.json"
    rules.write_text(json.dumps({"departments": {"legal": ["lawyer"]}}), encoding="utf-8")
    with (
        override_settings(RESEARCH_DEPARTMENT_RULES_FILE=str(rules)),
        pytest.raises(ValueError, match="unknown department"),
    ):
        taxonomy.rules()


def test_the_departments_match_the_framework_vocabulary() -> None:
    from apps.integrations.framework.normalized import DEPARTMENTS

    assert set(taxonomy.DEPARTMENTS) == set(DEPARTMENTS)


def test_the_campaign_factory_still_works_with_the_new_rules() -> None:
    assert make_campaign().pk
