"""Standard way to run work on the Django-Q2 worker and track its status.

Usage:

    @tracked_job(max_attempts=3, base_delay=5)
    def import_companies(job: BackgroundJob, campaign_id: int) -> dict[str, Any]:
        update_progress(job, 50)
        return {"imported": 120}

    job = enqueue(import_companies, campaign_id=7)   # returns a BackgroundJob immediately

Rules: task functions must be idempotent (ADR 0005), and arguments and results must be plain
JSON values (str, int, float, bool, None, list, dict). Pass ids, never model instances.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import async_task, schedule

from .models import BackgroundJob, JobStatus

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 2000

JobFunc = Callable[..., dict[str, Any] | None]


def ensure_json(value: object, what: str) -> None:
    """Raise TypeError unless `value` survives a JSON round trip unchanged."""
    try:
        round_tripped = json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{what} must be JSON serializable: {exc}") from exc
    if round_tripped != value:
        raise TypeError(f"{what} must be plain JSON values (tuples, sets, etc. are not allowed)")


def create_job(name: str) -> BackgroundJob:
    return BackgroundJob.objects.create(name=name)


def update_progress(job: BackgroundJob, percent: int) -> None:
    job.progress = max(0, min(100, percent))
    job.save(update_fields=["progress", "updated_at"])


def mark_running(job: BackgroundJob) -> None:
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.error = ""
    job.save(update_fields=["status", "attempts", "error", "updated_at"])


def mark_succeeded(job: BackgroundJob) -> None:
    job.status = JobStatus.SUCCEEDED
    job.progress = 100
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "progress", "finished_at", "updated_at"])


def mark_retrying(job: BackgroundJob, error: str) -> None:
    job.status = JobStatus.RETRYING
    job.error = error[:MAX_ERROR_LENGTH]
    job.save(update_fields=["status", "error", "updated_at"])


def mark_failed(job: BackgroundJob, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error[:MAX_ERROR_LENGTH]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at", "updated_at"])


def backoff_delay(attempt: int, base_delay: float) -> timedelta:
    """Exponential backoff: base, 2x base, 4x base, ... for attempt 1, 2, 3, ..."""
    return timedelta(seconds=base_delay * 2 ** (attempt - 1))


def func_path(func: Callable[..., Any]) -> str:
    return f"{func.__module__}.{func.__qualname__}"


def enqueue(func: JobFunc, *args: Any, name: str | None = None, **kwargs: Any) -> BackgroundJob:
    """Create a job record and queue `func` (a `@tracked_job` function) for the worker."""
    ensure_json([list(args), kwargs], "Task arguments")
    job = create_job(name or func.__name__)
    async_task(func_path(func), str(job.pk), *args, **kwargs)
    return job


def tracked_job(*, max_attempts: int = 3, base_delay: float = 5.0) -> Callable[[JobFunc], JobFunc]:
    """Wrap `func(job, *args, **kwargs)` as a worker task `task(job_id, *args, **kwargs)`.

    It records running/succeeded status, and on an exception either schedules a retry after an
    exponential backoff (status "retrying") or, once `max_attempts` runs have been used, marks
    the job "failed" and re-raises so Django-Q2 records the failure too. Django-Q2's own retry
    is not used (`ack_failures` is on), so each run is counted exactly once.
    """

    def decorator(func: JobFunc) -> JobFunc:
        @functools.wraps(func)
        def task(job_id: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            job = BackgroundJob.objects.get(pk=job_id)
            mark_running(job)
            try:
                result = func(job, *args, **kwargs)
                if result is not None:
                    ensure_json(result, "Task result")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if job.attempts < max_attempts:
                    delay = backoff_delay(job.attempts, base_delay)
                    mark_retrying(job, error)
                    schedule(
                        func_path(task),
                        job_id,
                        *args,
                        name=f"retry-{job_id}-{job.attempts}",
                        schedule_type=Schedule.ONCE,
                        next_run=timezone.now() + delay,
                        **kwargs,
                    )
                    logger.warning(
                        "Job %s failed (attempt %s/%s), retrying in %s: %s",
                        job_id,
                        job.attempts,
                        max_attempts,
                        delay,
                        error,
                    )
                    return None
                mark_failed(job, error)
                logger.error("Job %s failed permanently: %s", job_id, error)
                raise
            mark_succeeded(job)
            return result

        return task

    return decorator
