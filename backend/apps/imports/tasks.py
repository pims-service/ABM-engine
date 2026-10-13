"""Worker tasks of the imports app. Found automatically at startup (``autodiscover_modules``),
so the job types are registered in the api process and in the worker.

* ``process_csv_import`` (issue #62): the rows of an uploaded CSV.
* ``import_provider_batch`` (issue #67): a provider import larger than the inline threshold.
  The task links the job to the batch, runs every row through ``run_provider_import``
  (idempotent per row, so a retry or redelivery never duplicates) and mirrors the batch counters
  onto the job. After the last allowed attempt the batch is ended ``failed`` so it never stays
  ``processing``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from apps.accounts.models import User
from apps.core.jobs import record_progress, set_total, tracked_job
from apps.core.models import Job

from .csv_import import JOB_TYPE, MAX_ATTEMPTS, run_csv_import
from .models import ImportBatch
from .provider_import import _audit_finished, run_provider_import
from .services import attach_job, fail_batch

PROVIDER_MAX_ATTEMPTS = 3


@tracked_job(job_type=JOB_TYPE, max_attempts=MAX_ATTEMPTS, base_delay=5)
def process_csv_import(job: Job, batch_id: str) -> dict[str, Any]:
    """Process every row of an uploaded CSV into its campaign (issue #62).

    Call it through ``apps.imports.csv_import.submit_csv_import``. Idempotent: a retry or a
    redelivery after a worker crash skips the rows already recorded and carries on. See
    ``apps/imports/csv_import.py`` for the details.
    """
    return run_csv_import(job, batch_id)


@tracked_job(job_type="provider_import", max_attempts=PROVIDER_MAX_ATTEMPTS, base_delay=10)
def import_provider_batch(
    job: Job, batch_id: str, entries: Sequence[dict[str, Any]], user_id: str | None = None
) -> dict[str, Any]:
    batch = ImportBatch.objects.get(pk=batch_id)
    attach_job(batch, job)
    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        outcome = run_provider_import(batch, entries, user=user)
    except Exception:
        job.refresh_from_db()
        batch.refresh_from_db()
        if job.attempts >= PROVIDER_MAX_ATTEMPTS and not batch.is_finished:
            fail_batch(batch, "The import could not be completed. Please try again.")
            _audit_finished(batch)
        raise
    batch = outcome.batch
    job.refresh_from_db()
    set_total(job, batch.total_count)
    failed = batch.failed_count
    done = batch.processed_count - failed
    record_progress(job, done=done - job.done_count, failed=failed - job.failed_count)
    return {"batch_id": str(batch.pk), "status": batch.status}
