"""Robustness under load: dead workers do not block work, and the dashboard stays cheap."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.companies.analysis import request_analysis
from apps.core.jobs import reap_stale_jobs
from apps.core.models import Job, JobStatus
from tests.factories import make_campaign, make_client, make_company
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]


def _age(job: Job, **fields: object) -> None:
    Job.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(days=2), **fields)


def test_a_dead_running_job_is_closed_and_the_company_can_be_analyzed_again() -> None:
    company = make_company()
    first = request_analysis(company).job
    _age(first, status=JobStatus.RUNNING)
    second = request_analysis(company)
    assert second.created
    first.refresh_from_db()
    assert first.status == JobStatus.FAILED
    assert "worker stopped" in first.error_summary


def test_a_fresh_running_job_is_left_alone() -> None:
    company = make_company()
    job = request_analysis(company).job
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RUNNING)
    assert reap_stale_jobs() == 0
    assert request_analysis(company).created is False


def test_summary_uses_a_constant_number_of_queries() -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    for _ in range(30):
        make_company(campaign=campaign)
    api = api_for("viewer", client)
    url = f"/api/v1/campaigns/{campaign.pk}/accounts/summary/"
    with CaptureQueriesContext(connection) as queries:
        response = api.get(url)
    assert response.status_code == 200
    assert response.json()["total"] == 30
    assert response.json()["not_assessed"] == 30
    assert len(queries) <= 12
