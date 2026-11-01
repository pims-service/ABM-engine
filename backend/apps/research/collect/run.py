"""One research run of one company: the steps in order, each recorded on the job (issue #91).

The steps are ``profile`` (company data from the provider), ``signals`` (provider signals, funding
and hiring) and ``news``. Each is a ``JobItem`` of the run's ``Job``, so the job shows which step
did what, and a step that fails does not stop the others: the job ends ``partial`` and the items
that worked keep their results. A step that cannot run because nothing is configured fails (or,
for news, is skipped) with a message that says what to configure; nothing is invented.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from apps.companies.models import Company
from apps.core.jobs import complete_item, fail_item, start_item
from apps.core.models import ITEM_TERMINAL, Job, JobItem
from apps.integrations.company_data_connection import CompanyDataUnavailable, get_adapter
from apps.research.collect.news import collect_news
from apps.research.collect.profile import ProfileOutcome, collect_profile
from apps.research.collect.signals import collect_signals

PROFILE = "profile"
SIGNALS = "signals"
NEWS = "news"
#: In run order. Signals use what the profile step found, so profile goes first.
STEPS = (PROFILE, SIGNALS, NEWS)
SUBJECT_TYPE = "research_step"


def step_id(company_id: Any, step: str) -> uuid.UUID:
    """The id of a step's ``JobItem`` subject: stable per company and step."""
    return uuid.uuid5(uuid.UUID(str(company_id)), f"research:{step}")


def step_of(company_id: Any, item: JobItem) -> str | None:
    return next((s for s in STEPS if step_id(company_id, s) == item.subject_id), None)


def run_research(job: Job, company: Company) -> dict[str, Any]:
    """Run every step that has not finished yet (a redelivered task resumes, never repeats)."""
    items = {step_of(company.pk, item): item for item in job.items.all()}
    state: dict[str, Any] = {"profile": None, "adapter": None}
    report: dict[str, Any] = {"company_id": str(company.pk)}
    for step in STEPS:
        item = items.get(step)
        if item is None or item.status in ITEM_TERMINAL:
            continue
        start_item(item)
        try:
            result = _STEP[step](company, job, state)
        except CompanyDataUnavailable as exc:
            fail_item(item, str(exc))
            report[step] = "unavailable"
        except Exception as exc:
            fail_item(item, f"{type(exc).__name__}: {exc}"[:1000])
            report[step] = "failed"
        else:
            complete_item(item, result)
            report[step] = result.get("status", "done")
    return report


def _adapter(company: Company, state: dict[str, Any]) -> Any:
    if state["adapter"] is None:
        state["adapter"] = get_adapter(company.client_id)
    return state["adapter"]


def _profile(company: Company, job: Job, state: dict[str, Any]) -> dict[str, Any]:
    outcome = collect_profile(company, _adapter(company, state), user=job.created_by)
    state["profile"] = outcome
    return {
        "status": outcome.status,
        "missing_fields": outcome.missing_fields,
        "snapshot_id": str(outcome.snapshot.pk) if outcome.snapshot else None,
    }


def _signals(company: Company, job: Job, state: dict[str, Any]) -> dict[str, Any]:
    profile: ProfileOutcome | None = state["profile"]
    outcome = collect_signals(company, _adapter(company, state), profile, user=job.created_by)
    return {"status": "done", **outcome.as_dict()}


def _news(company: Company, job: Job, state: dict[str, Any]) -> dict[str, Any]:
    outcome = collect_news(company, user=job.created_by)
    return {"status": "skipped" if outcome.skipped_reason else "done", **outcome.as_dict()}


_STEP: dict[str, Callable[[Company, Job, dict[str, Any]], dict[str, Any]]] = {
    PROFILE: _profile,
    SIGNALS: _signals,
    NEWS: _news,
}
