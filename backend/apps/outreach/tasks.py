"""Worker task of the outreach app, found automatically at startup (``autodiscover_modules``).

``push_accounts_to_crm`` pushes approved accounts to the client's CRM as a ``provider_task``: a
rate-limited or unavailable CRM is waited for, a rejected token ends the job at once, and one
account the CRM refuses fails only that account. Queued by ``apps.outreach.crm_jobs``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from apps.companies.models import Company
from apps.core.models import JobItem
from apps.integrations.crm_connection import crm_registry
from apps.integrations.framework import CRMContact
from apps.integrations.tasks import ProviderJobContext, provider_task

from .crm_jobs import JOB_TYPE, SUBJECT_TYPE
from .crm_push import PushBlocked, push_account

#: Stop between accounts well before Django-Q2's hard limit; the rest is re-queued.
SOFT_LIMIT = max(10, int(settings.Q_CLUSTER["timeout"] * 0.8))


class _ResilientCrm:
    """The adapter as ``push_account`` sees it: every push is metered and goes through the
    job's retries, rate limit and circuit breaker."""

    def __init__(self, ctx: ProviderJobContext) -> None:
        self._ctx = ctx

    def push_contact(self, contact: CRMContact) -> Any:
        ctx = self._ctx
        return ctx.call_metered(ctx.adapter.push_contact, contact)  # type: ignore[attr-defined]


@provider_task(
    job_type=JOB_TYPE,
    category="crm",
    provider="gohighlevel",
    registry=crm_registry(),
    soft_time_limit=SOFT_LIMIT,
)
def push_accounts_to_crm(ctx: ProviderJobContext, company_ids: list[str]) -> dict[str, Any]:
    """Push each approved account's primary person with a note of the research and messages.

    An account that is no longer ready (decision changed, message withdrawn, archived) is
    skipped and the reason kept on the item. Pushing twice updates the same CRM contact."""
    adapter = _ResilientCrm(ctx)

    def push(item: JobItem) -> dict[str, Any]:
        company = (
            Company.objects.select_related("campaign__client").filter(pk=item.subject_id).first()
        )
        if company is None:
            return {"skipped": "This account no longer exists."}
        try:
            pushed = push_account(company, adapter, ctx.job.created_by)
        except PushBlocked as exc:
            return {"skipped": "; ".join(exc.messages)}
        return {
            "action": pushed.action,
            "remote_id": pushed.remote_id,
            "exported_messages": pushed.exported,
        }

    ctx.process_items([(SUBJECT_TYPE, cid) for cid in company_ids], push)
    ctx.job.refresh_from_db()
    return {"processed": ctx.job.processed_count}
