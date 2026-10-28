"""Worker task for the "Analyze Company" action (issues #59 and the qualification engine).

The API creates a ``company_analysis`` job with one item per company; this task runs the engine
(``apps.research.engine.run_analysis``) for it. The contract of the job rows is unchanged. Two
outcomes are not a failure of the company: no usable AI connection (the item fails with a message
telling the user what to configure, and the company stays ``pending``), and a site that cannot be
read (the item fails and the company is marked ``failed``). Nothing is invented in either case.

The email steps of an approved account run here too (``find_contact_emails`` and
``verify_contact_emails``): ``provider_task`` jobs, so a provider that is rate limited, down or out
of budget is waited for or ends the job cleanly instead of blocking a web request. Queued by
``apps.companies.enrichment``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.companies.models import Company, CompanyStatus, Contact, EmailStatus
from apps.core.jobs import complete_item, fail_item, start_item, tracked_job
from apps.core.models import ITEM_TERMINAL, Job, JobItem, JobItemStatus
from apps.integrations.framework import PersonRef
from apps.integrations.people_connection import (
    EMAIL,
    PROVIDER,
    VERIFY,
    metered_operation,
    people_registry,
)
from apps.integrations.tasks import ProviderJobContext, provider_task

from . import enrichment

ANALYSIS_JOB_TYPE = "company_analysis"


def _set_status(company_id: str, status: str) -> None:
    Company.objects.filter(pk=company_id).update(status=status, updated_at=timezone.now())


@tracked_job(job_type=ANALYSIS_JOB_TYPE, max_attempts=1)
def analyze_company(job: Job, company_id: str) -> dict[str, Any]:
    """Research one company and record its ICP assessment and AI recommendation."""
    # Imported here: the research app imports the company models at load time.
    from apps.research.engine import (
        AnalysisFailed,
        AnalysisUnavailable,
        get_llm_adapter,
        run_analysis,
    )

    company = Company.objects.select_related("campaign__current_profile").get(pk=company_id)
    result: dict[str, Any] = {"company_id": company_id}
    for item in job.items.exclude(status__in=ITEM_TERMINAL):
        start_item(item)
        try:
            adapter = get_llm_adapter(
                company.client_id, job=job, campaign=company.campaign, user=job.created_by
            )
            _set_status(company_id, CompanyStatus.ANALYZING)
            outcome = run_analysis(company, adapter, user=job.created_by)
        except AnalysisUnavailable as exc:
            _set_status(company_id, CompanyStatus.PENDING)
            fail_item(item, str(exc))
            result["error"] = "unavailable"
        except AnalysisFailed as exc:
            _set_status(company_id, CompanyStatus.FAILED)
            fail_item(item, f"{exc.code}: {exc.message}")
            result["error"] = exc.code
        else:
            _set_status(company_id, CompanyStatus.ANALYZED)
            summary = {
                "fit": outcome.assessment.fit,
                "recommendation": outcome.recommendation.status,
                "signals": len(outcome.signals),
            }
            complete_item(item, summary)
            result.update(summary)
    return result


# ------------------------------------------------------------------ email steps (Brief section 12)

#: Stop between people well before Django-Q2's hard limit; the rest is re-queued, nothing is lost.
SOFT_LIMIT = max(10, int(settings.Q_CLUSTER["timeout"] * 0.8))


def _load_contact(item: JobItem) -> Contact | None:
    return (
        Contact.objects.select_related("company__campaign", "data_source")
        .filter(pk=item.subject_id)
        .first()
    )


def _metered(ctx: ProviderJobContext, operation: Any) -> Any:
    """``operation`` with usage logging and the budget check, ready for ``ctx.call``."""
    return metered_operation(
        ctx.connection,
        ctx.adapter,
        operation,
        job=ctx.job,
        campaign=ctx.job.campaign,
        user=ctx.job.created_by,
    )


@provider_task(
    job_type=enrichment.FIND_JOB_TYPE,
    category=EMAIL,
    provider=PROVIDER[EMAIL],
    registry=people_registry(),
    soft_time_limit=SOFT_LIMIT,
)
def find_contact_emails(
    ctx: ProviderJobContext, contact_ids: list[str], verify_after: bool = False
) -> dict[str, Any]:
    """Reveal the email of each person (approved accounts only), saved as ``unverified``.

    A person who can no longer take the step (the decision changed, an email appeared, the record
    was archived) is skipped and the reason kept on the item. With ``verify_after`` the people
    whose email was found go straight on to a ``verify_contact_emails`` job."""

    def find(item: JobItem) -> dict[str, Any]:
        from apps.companies import services

        contact = _load_contact(item)
        reason = (
            "This person no longer exists."
            if contact is None
            else enrichment.blocker(contact, "find")
        )
        if contact is None or reason:
            return {"skipped": reason}
        company = contact.company
        reference = contact.data_source.provider_reference
        prefix = f"{ctx.adapter.key}:"
        if contact.profile_url:
            ref = PersonRef(
                contact.name, title=contact.title or None, profile_url=contact.profile_url
            )
            result = ctx.call(
                _metered(ctx, ctx.adapter.find_email),  # type: ignore[attr-defined]
                ref,
                company.domain or "",
            )
        else:
            result = ctx.call(
                _metered(ctx, ctx.adapter.find_email_by_uid),  # type: ignore[attr-defined]
                reference[len(prefix) :],
            )
        email = result.data.email if result.data else None
        if email:
            services.update_contact(contact, email=email, email_status=EmailStatus.UNVERIFIED)
            return {"found": True}
        services.update_contact(contact, email_status=EmailStatus.NOT_FOUND)
        return {"found": False}

    ctx.process_items([(enrichment.SUBJECT_TYPE, cid) for cid in contact_ids], find)
    ctx.job.refresh_from_db()
    follow_up = 0
    if verify_after and ctx.job.campaign is not None:
        found = [
            str(item.subject_id)
            for item in ctx.job.items.filter(status=JobItemStatus.SUCCEEDED, result__found=True)
        ]
        busy = enrichment.open_contact_ids(enrichment.VERIFY_JOB_TYPE, found)
        fresh = [cid for cid in found if cid not in {str(b) for b in busy}]
        if fresh:
            enrichment.queue_contact_job(
                enrichment.VERIFY_JOB_TYPE,
                verify_contact_emails,
                ctx.job.campaign,
                ctx.job.created_by,
                fresh,
            )
            follow_up = len(fresh)
    return {"processed": ctx.job.processed_count, "verification_queued": follow_up}


@provider_task(
    job_type=enrichment.VERIFY_JOB_TYPE,
    category=VERIFY,
    provider=PROVIDER[VERIFY],
    registry=people_registry(),
    soft_time_limit=SOFT_LIMIT,
)
def verify_contact_emails(ctx: ProviderJobContext, contact_ids: list[str]) -> dict[str, Any]:
    """Check the email of each person with the verifier (approved accounts only).

    Only a clean ``valid`` becomes ``verified`` and an undeliverable address ``invalid``; anything
    uncertain stays ``unverified``. People that can no longer take the step are skipped."""

    def verify(item: JobItem) -> dict[str, Any]:
        from apps.companies import services
        from apps.companies.lookup import email_status_for

        contact = _load_contact(item)
        reason = (
            "This person no longer exists."
            if contact is None
            else enrichment.blocker(contact, "verify")
        )
        if contact is None or reason:
            return {"skipped": reason}
        result = ctx.call(
            _metered(ctx, ctx.adapter.verify),  # type: ignore[attr-defined]
            contact.email,
        )
        verdict = result.data.status if result.data else "unknown"
        services.update_contact(contact, email_status=email_status_for(verdict))
        return {"verdict": verdict}

    ctx.process_items([(enrichment.SUBJECT_TYPE, cid) for cid in contact_ids], verify)
    ctx.job.refresh_from_db()
    return {"processed": ctx.job.processed_count}
