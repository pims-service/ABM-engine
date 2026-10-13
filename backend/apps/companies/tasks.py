"""Worker task for the "Analyze Company" action (issue #59).

The research engine does not exist yet (milestone M4). This task is the contract only: it takes
the job the API created, and ends every item as failed with ``ANALYSIS_NOT_IMPLEMENTED``. It
researches nothing and writes no company data, so no result can be mistaken for real research.
M4 replaces the body of ``analyze_company`` (same signature, same job type, same item per
company) and the API, the job rows and the tests of the contract stay as they are.
"""

from __future__ import annotations

from typing import Any

from apps.core.jobs import fail_item, start_item, tracked_job
from apps.core.models import ITEM_TERMINAL, Job

ANALYSIS_JOB_TYPE = "company_analysis"
ANALYSIS_NOT_IMPLEMENTED = (
    "Company analysis is not implemented yet (the research engine arrives in milestone M4)."
)


@tracked_job(job_type=ANALYSIS_JOB_TYPE, max_attempts=1)
def analyze_company(job: Job, company_id: str) -> dict[str, Any]:
    """Placeholder: mark the company's item failed with a clear reason (see module docstring)."""
    for item in job.items.exclude(status__in=ITEM_TERMINAL):
        start_item(item)
        fail_item(item, ANALYSIS_NOT_IMPLEMENTED)
    return {"implemented": False, "company_id": company_id}
