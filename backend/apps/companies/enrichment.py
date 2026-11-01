"""Background email steps for approved accounts (Brief section 12): find an email, verify it.

The single-person endpoints (``lookup.find_email_for`` / ``verify_email_for``) answer inside the web
request. This module is the queueing half of the same two steps for many people at once, run by
the worker tasks in ``apps.companies.tasks``:

* Who is covered: the primary and secondary targets of accounts a human decided ADD for. Other
  found people are never looked up in bulk (each lookup can cost a credit).
* ``blocker`` is the one place that says why a person cannot take a step. The request uses it to
  choose who to queue and the worker uses it again just before each call, because a decision, an
  email or an archive may have changed while the job waited.
* Nobody is queued twice: a person with an open (queued or running) job of the same type is
  counted as ``already_running``.
* A missing or unsupported connection is refused up front with a plain message, not queued.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django_q.tasks import async_task

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.core.jobs import add_items, create_job, func_path, reap_stale_jobs
from apps.core.models import Job, JobItem, JobStatus
from apps.integrations.people_connection import (
    EMAIL,
    PROVIDER,
    VERIFY,
    active_provider,
    connection_problem,
)
from apps.outreach.decisions import ADD, latest_human_decision

from .models import Company, Contact, ContactRole, EmailStatus

SUBJECT_TYPE = "contact"
FIND_JOB_TYPE = "find_contact_emails"
VERIFY_JOB_TYPE = "verify_contact_emails"
MAX_CONTACTS = 300
TARGET_ROLES = (ContactRole.PRIMARY, ContactRole.SECONDARY)

#: What the API accepts as ``step``, and the single steps each one runs.
FIND_EMAILS = "find_emails"
VERIFY_EMAILS = "verify_emails"
FIND_AND_VERIFY = "find_and_verify"
STEP_CHOICES = (FIND_EMAILS, VERIFY_EMAILS, FIND_AND_VERIFY)
_FIND = "find"
_VERIFY = "verify"
_STEPS = {
    FIND_EMAILS: (_FIND,),
    VERIFY_EMAILS: (_VERIFY,),
    FIND_AND_VERIFY: (_FIND, _VERIFY),
}
_CATEGORY = {_FIND: EMAIL, _VERIFY: VERIFY}
_JOB_TYPE = {_FIND: FIND_JOB_TYPE, _VERIFY: VERIFY_JOB_TYPE}
#: An answer that is final: a second check would only spend another credit.
_CHECKED = (EmailStatus.VERIFIED, EmailStatus.INVALID)


@dataclass(frozen=True)
class EnrichmentRequest:
    """``queued`` people went to the worker, ``already_running`` were already waiting in an open
    job, ``not_ready`` could take no step (no approval, no email or lookup reference, ...)."""

    queued: int
    already_running: int
    not_ready: int
    job_ids: list[str]


def blocker(
    contact: Contact,
    step: str,
    *,
    approved: bool | None = None,
    provider: str | None = None,
) -> str | None:
    """Why ``contact`` cannot take ``step`` (``"find"`` or ``"verify"``), or ``None``.

    ``approved`` skips the decision lookup when the caller already knows the account is ADD;
    ``provider`` is the email provider in use (looked up when not given)."""
    company = contact.company
    if company.is_archived or contact.is_archived:
        return "Archived records are read-only."
    if approved is None:
        approved = latest_human_decision(company) == ADD
    if not approved:
        return "The account is not approved: decide ADD first."
    if step == _FIND:
        if contact.email:
            return "This person already has an email."
        provider = provider or active_provider(EMAIL, company.campaign.client_id) or PROVIDER[EMAIL]
        return lookup_blocker(contact, provider)
    if not contact.email:
        return "This person has no email to verify."
    if contact.email_status in _CHECKED:
        return "The email was already checked."
    return None


def lookup_blocker(contact: Contact, provider: str) -> str | None:
    """Why ``provider`` cannot look up this person's email, or ``None``.

    Hunter works from the full name and the company's domain; SignalHire needs a profile link or
    one of its own ids."""
    if provider == "hunter":
        if len(contact.name.split()) < 2:
            return "Hunter needs the person's first and last name."
        if not contact.company.domain:
            return "Hunter needs the company's website to look this person up."
        return None
    prefix = f"{provider}:"
    if not contact.profile_url and not contact.data_source.provider_reference.startswith(prefix):
        return "There is no profile link or provider id to look this person up with."
    return None


def open_contact_ids(job_type: str, ids: Sequence[Any]) -> set[Any]:
    """People with an item in a queued or running job of ``job_type``, in ONE query."""
    return set(
        JobItem.objects.filter(
            subject_type=SUBJECT_TYPE,
            subject_id__in=list(ids),
            job__type=job_type,
            job__status__in=[JobStatus.QUEUED, JobStatus.RUNNING],
        ).values_list("subject_id", flat=True)
    )


def queue_contact_job(
    job_type: str,
    task: Any,
    campaign: Campaign,
    user: User | None,
    contact_ids: Sequence[Any],
    **kwargs: Any,
) -> Job:
    """Create the job with one item per person and hand it to the worker."""
    with transaction.atomic():
        job = create_job(job_type, client=campaign.client, campaign=campaign, created_by=user)
        add_items(job, [(SUBJECT_TYPE, cid) for cid in contact_ids])
    task_id = async_task(func_path(task), str(job.pk), [str(cid) for cid in contact_ids], **kwargs)
    # Do not touch status: a sync-mode run has already finished the job.
    Job.objects.filter(pk=job.pk).update(queue_task_id=str(task_id or "")[:64])
    job.refresh_from_db()
    return job


def request_contact_enrichment(
    campaign: Campaign, companies: Sequence[Company], step: str, user: User | None
) -> EnrichmentRequest:
    """Queue ``step`` for the target people of ``companies`` (already limited to ADD accounts).

    Raises ``ValidationError`` for an unknown step or a missing connection. The people are the
    first ``MAX_CONTACTS`` by company name; send the rest in another request."""
    # Imported here: the tasks module imports this one.
    from .tasks import find_contact_emails, verify_contact_emails

    if step not in _STEPS:
        raise ValidationError(f"Unknown step '{step}'.")
    steps = _STEPS[step]
    for single in steps:
        if (problem := connection_problem(_CATEGORY[single], campaign.client_id)) is not None:
            raise ValidationError(problem)
    reap_stale_jobs(campaign_id=campaign.pk)
    contacts = list(
        Contact.objects.active()
        .filter(company__in=[c.pk for c in companies], role__in=TARGET_ROLES)
        .select_related("company", "data_source")
        .order_by("company__name", "role", "id")[:MAX_CONTACTS]
    )
    provider = active_provider(EMAIL, campaign.client_id) or PROVIDER[EMAIL]
    ready = {
        single: [
            c for c in contacts if blocker(c, single, approved=True, provider=provider) is None
        ]
        for single in steps
    }
    served = {c.pk for people in ready.values() for c in people}
    queued = running = 0
    job_ids: list[str] = []
    for single in steps:
        busy = open_contact_ids(_JOB_TYPE[single], [c.pk for c in ready[single]])
        fresh = [c.pk for c in ready[single] if c.pk not in busy]
        running += len(ready[single]) - len(fresh)
        if not fresh:
            continue
        if single == _FIND:
            job = queue_contact_job(
                FIND_JOB_TYPE,
                find_contact_emails,
                campaign,
                user,
                fresh,
                verify_after=step == FIND_AND_VERIFY,
            )
        else:
            job = queue_contact_job(VERIFY_JOB_TYPE, verify_contact_emails, campaign, user, fresh)
        queued += len(fresh)
        job_ids.append(str(job.pk))
    return EnrichmentRequest(
        queued=queued,
        already_running=running,
        not_ready=len(contacts) - len(served),
        job_ids=job_ids,
    )
