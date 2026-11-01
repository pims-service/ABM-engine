"""Worker task of a research run (issue #91). The job and its items are made by
``apps.research.orchestrator.request_research``; this runs the steps (``collect.run``)."""

from __future__ import annotations

from typing import Any

from apps.companies.models import Company
from apps.core.jobs import tracked_job
from apps.core.models import Job

RESEARCH_JOB_TYPE = "company_research"


@tracked_job(job_type=RESEARCH_JOB_TYPE, max_attempts=1)
def run_company_research(job: Job, company_id: str) -> dict[str, Any]:
    """Collect the provider profile, signals and news of one company."""
    from apps.research.collect.run import run_research

    company = Company.objects.select_related("client").get(pk=company_id)
    return run_research(job, company)
