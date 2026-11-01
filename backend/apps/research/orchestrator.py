"""Request a research run of one company (issue #91): the queueing half, like ``request_analysis``.

A run is a ``core.Job`` of type ``company_research`` with one ``JobItem`` per step (see
``collect.run``), handed to the Django-Q2 worker (``apps.research.tasks``). Permission checks
belong to the API layer.

* Idempotent: while the company has a queued or running research job, that job is returned
  (``created=False``) and nothing is queued again.
* An archived company, campaign or client is refused (``ValidationError``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django_q.tasks import async_task

from apps.accounts.models import User
from apps.companies.models import Company
from apps.core.jobs import add_items, create_job, func_path, reap_stale_jobs
from apps.core.models import Job, JobItem, JobStatus
from apps.research.collect.run import PROFILE, STEPS, SUBJECT_TYPE, step_id
from apps.research.tasks import RESEARCH_JOB_TYPE, run_company_research


@dataclass(frozen=True)
class ResearchRequest:
    """``created`` is False when an already queued or running job was returned."""

    job: Job
    created: bool


def active_research_job(company: Company) -> Job | None:
    item = (
        JobItem.objects.filter(
            subject_type=SUBJECT_TYPE,
            subject_id=step_id(company.pk, PROFILE),
            job__type=RESEARCH_JOB_TYPE,
            job__status__in=[JobStatus.QUEUED, JobStatus.RUNNING],
        )
        .select_related("job")
        .order_by("-created_at")
        .first()
    )
    return item.job if item else None


def research_runs(company: Company, limit: int = 20) -> list[Job]:
    """The company's research runs, newest first, each with its step items loaded."""
    ids = (
        JobItem.objects.filter(
            subject_type=SUBJECT_TYPE,
            subject_id=step_id(company.pk, PROFILE),
            job__type=RESEARCH_JOB_TYPE,
        )
        .order_by("-created_at")
        .values_list("job_id", flat=True)[:limit]
    )
    jobs = Job.objects.filter(pk__in=list(ids)).prefetch_related("items")
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)


def request_research(company: Company, user: User | None = None) -> ResearchRequest:
    """Queue a research run of ``company`` (or return the one already queued or running)."""
    if company.is_archived:
        raise ValidationError("Archived companies cannot be researched; restore the company first.")
    reap_stale_jobs(campaign_id=company.campaign_id)
    with transaction.atomic():
        locked = Company.objects.select_for_update().select_related("campaign").get(pk=company.pk)
        existing = active_research_job(locked)
        if existing is not None:
            return ResearchRequest(existing, created=False)
        job = create_job(
            RESEARCH_JOB_TYPE,
            client=locked.campaign.client,
            campaign=locked.campaign,
            created_by=user,
        )
        add_items(job, [(SUBJECT_TYPE, step_id(locked.pk, step)) for step in STEPS])
    task_id: Any = async_task(func_path(run_company_research), str(job.pk), str(company.pk))
    Job.objects.filter(pk=job.pk).update(queue_task_id=str(task_id or "")[:64])
    job.refresh_from_db()
    return ResearchRequest(job, created=True)
