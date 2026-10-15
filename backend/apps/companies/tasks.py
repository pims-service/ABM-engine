"""Worker task for the "Analyze Company" action (issues #59 and the qualification engine).

The API creates a ``company_analysis`` job with one item per company; this task runs the engine
(``apps.research.engine.run_analysis``) for it. The contract of the job rows is unchanged. Two
outcomes are not a failure of the company: no usable AI connection (the item fails with a message
telling the user what to configure, and the company stays ``pending``), and a site that cannot be
read (the item fails and the company is marked ``failed``). Nothing is invented in either case.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.companies.models import Company, CompanyStatus
from apps.core.jobs import complete_item, fail_item, start_item, tracked_job
from apps.core.models import ITEM_TERMINAL, Job

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
