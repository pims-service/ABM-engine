"""Research history and the diff between snapshots (Brief section 19)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.companies.models import CompanyResearch
from apps.research.history import diff
from tests.factories import make_campaign, make_client, make_company, make_research
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]


def snapshots(company):
    now = timezone.now()
    old = make_research(
        company=company,
        researched_at=now - timedelta(days=90),
        employee_count=155,
        headquarters="Riyadh",
        sales_headcount=10,
    )
    new = make_research(
        company=company,
        researched_at=now,
        employee_count=140,
        headquarters="Riyadh",
        sales_headcount=None,
    )
    return old, new


def test_the_diff_reports_changed_added_and_removed_fields() -> None:
    company = make_company()
    old, new = snapshots(company)
    changes = {c["field"]: (c["old"], c["new"]) for c in diff(old, new)}
    assert changes["employee_count"] == ("155", "140")
    assert changes["sales_headcount"] == ("10", None)
    assert "headquarters" not in changes


def test_a_field_found_in_neither_snapshot_is_not_a_change() -> None:
    company = make_company()
    a = make_research(company=company, employee_count=None)
    b = make_research(company=company, employee_count=None)
    assert all(c["field"] != "employee_count" for c in diff(a, b))


def test_the_history_lists_snapshots_newest_first_and_keeps_the_old_ones() -> None:
    client = make_client()
    company = make_company(campaign=make_campaign(client=client))
    old, new = snapshots(company)
    data = api_for("viewer", client).get(f"/api/v1/companies/{company.pk}/research-history/").json()
    assert [row["id"] for row in data] == [str(new.pk), str(old.pk)]
    assert data[1]["first"] is True
    assert data[0]["changes"]
    assert CompanyResearch.objects.filter(company=company).count() == 2


def test_another_clients_history_is_not_visible() -> None:
    other = make_company()
    api = api_for("viewer", make_client())
    assert api.get(f"/api/v1/companies/{other.pk}/research-history/").status_code == 404
