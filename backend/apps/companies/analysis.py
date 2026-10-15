"""Request an analysis of one company: the queueing half of "Analyze Company" (issue #59).

``request_analysis`` records the request as a ``core.Job`` (type ``company_analysis``, scoped to
the company's client and campaign) with one ``JobItem`` for the company, then hands the job to
the Django-Q2 worker (``apps.companies.tasks.analyze_company``). Permission checks belong to the
API layer.

* Idempotent: while the company has a queued or running analysis job, that job is returned
  (``created=False``) and nothing is queued again.
* An archived company, campaign or client is refused (``ValidationError``).
* The task is a placeholder until M4 (see ``tasks.py``): the job ends ``failed`` with a clear
  "not implemented yet" message and no data is invented.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django_q.tasks import async_task

from apps.accounts.models import User
from apps.core.jobs import add_items, create_job, func_path, reap_stale_jobs
from apps.core.models import Job, JobItem, JobStatus

from .models import Company
from .tasks import ANALYSIS_JOB_TYPE, analyze_company

SUBJECT_TYPE = "company"


@dataclass(frozen=True)
class AnalysisRequest:
    """``created`` is False when an already queued or running job was returned."""

    job: Job
    created: bool


def active_analysis_job(company: Company) -> Job | None:
    """The company's queued or running analysis job, if any."""
    item = (
        JobItem.objects.filter(
            subject_type=SUBJECT_TYPE,
            subject_id=company.pk,
            job__type=ANALYSIS_JOB_TYPE,
            job__status__in=[JobStatus.QUEUED, JobStatus.RUNNING],
        )
        .select_related("job")
        .order_by("-created_at")
        .first()
    )
    return item.job if item else None


def latest_analysis_jobs(company_ids: Iterable[Any]) -> dict[str, Job]:
    """``{company_id: newest analysis Job}`` for many companies in ONE query (list pages)."""
    latest: dict[str, Job] = {}
    items = (
        JobItem.objects.filter(
            subject_type=SUBJECT_TYPE,
            subject_id__in=list(company_ids),
            job__type=ANALYSIS_JOB_TYPE,
        )
        .select_related("job")
        .order_by("-created_at", "-id")
    )
    for item in items:
        latest.setdefault(str(item.subject_id), item.job)
    return latest


def request_analysis(company: Company, user: User | None = None) -> AnalysisRequest:
    """Queue an analysis of ``company`` (or return the one already queued or running)."""
    if company.is_archived:
        raise ValidationError("Archived companies cannot be analyzed; restore the company first.")
    reap_stale_jobs(campaign_id=company.campaign_id)
    with transaction.atomic():
        # Serialize concurrent requests for the same company on its row.
        locked = Company.objects.select_for_update().select_related("campaign").get(pk=company.pk)
        existing = active_analysis_job(locked)
        if existing is not None:
            return AnalysisRequest(existing, created=False)
        job = create_job(
            ANALYSIS_JOB_TYPE,
            client=locked.campaign.client,
            campaign=locked.campaign,
            created_by=user,
        )
        add_items(job, [(SUBJECT_TYPE, locked.pk)])
    task_id = async_task(func_path(analyze_company), str(job.pk), str(company.pk))
    # Do not touch status: a sync-mode run has already finished the job.
    Job.objects.filter(pk=job.pk).update(queue_task_id=str(task_id or "")[:64])
    job.refresh_from_db()
    return AnalysisRequest(job, created=True)
