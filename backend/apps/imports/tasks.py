"""Worker tasks of the imports app. Found automatically at startup (``autodiscover_modules``),
so the job type is registered in the api process and in the worker."""

from __future__ import annotations

from typing import Any

from apps.core.jobs import tracked_job
from apps.core.models import Job

from .csv_import import JOB_TYPE, MAX_ATTEMPTS, run_csv_import


@tracked_job(job_type=JOB_TYPE, max_attempts=MAX_ATTEMPTS, base_delay=5)
def process_csv_import(job: Job, batch_id: str) -> dict[str, Any]:
    """Process every row of an uploaded CSV into its campaign (issue #62).

    Call it through ``apps.imports.csv_import.submit_csv_import``. Idempotent: a retry or a
    redelivery after a worker crash skips the rows already recorded and carries on. See
    ``apps/imports/csv_import.py`` for the details.
    """
    return run_csv_import(job, batch_id)
