"""Push approved accounts to the CRM in the background (Brief section 12), many at once.

``POST .../accounts/push-to-crm/`` pushes inside the request and answers with the result;
this is the queueing half of the same push for the worker task ``push_accounts_to_crm``
(``apps.outreach.tasks``), so a slow or rate-limited CRM never holds up a web request.

* One item per account. Accounts that cannot be pushed yet (no primary person, no email, no
  approved message) are counted as ``not_ready`` and not queued; the worker checks again before
  each push, because a decision or a message may have changed while the job waited.
* An account with an open (queued or running) push job is counted as ``already_running``.
* A missing or unsupported CRM connection is refused up front with a plain message.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django_q.tasks import async_task

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.core.jobs import add_items, create_job, func_path, reap_stale_jobs
from apps.core.models import Job, JobItem, JobStatus
from apps.integrations.crm_connection import crm_problem

from .crm_push import not_ready_reason

SUBJECT_TYPE = "company"
JOB_TYPE = "push_accounts_to_crm"
MAX_ACCOUNTS = 100


@dataclass(frozen=True)
class CrmPushRequest:
    queued: int
    already_running: int
    not_ready: int
    job_id: str | None


def request_crm_push(
    campaign: Campaign, companies: Sequence[Company], user: User | None
) -> CrmPushRequest:
    """Queue one push job for the ready accounts among ``companies`` (the first
    ``MAX_ACCOUNTS`` are looked at). Raises ``ValidationError`` when no CRM is usable."""
    # Imported here: the tasks module imports this one.
    from .tasks import push_accounts_to_crm

    if (problem := crm_problem(campaign.client_id)) is not None:
        raise ValidationError(problem)
    reap_stale_jobs(campaign_id=campaign.pk)
    picked = list(companies)[:MAX_ACCOUNTS]
    busy = set(
        JobItem.objects.filter(
            subject_type=SUBJECT_TYPE,
            subject_id__in=[c.pk for c in picked],
            job__type=JOB_TYPE,
            job__status__in=[JobStatus.QUEUED, JobStatus.RUNNING],
        ).values_list("subject_id", flat=True)
    )
    ready: list[Company] = []
    running = not_ready = 0
    for company in picked:
        if company.pk in busy:
            running += 1
        elif not_ready_reason(company) is not None:
            not_ready += 1
        else:
            ready.append(company)
    if not ready:
        return CrmPushRequest(0, running, not_ready, None)
    with transaction.atomic():
        job = create_job(JOB_TYPE, client=campaign.client, campaign=campaign, created_by=user)
        add_items(job, [(SUBJECT_TYPE, c.pk) for c in ready])
    ids = [str(c.pk) for c in ready]
    task_id = async_task(func_path(push_accounts_to_crm), str(job.pk), ids)
    # Do not touch status: a sync-mode run has already finished the job.
    Job.objects.filter(pk=job.pk).update(queue_task_id=str(task_id or "")[:64])
    return CrmPushRequest(len(ready), running, not_ready, str(job.pk))
