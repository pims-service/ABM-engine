"""Run work on the Django-Q2 worker and track it in a ``Job`` (issue #44, builds on #25).

A task is a ``@tracked_job`` function that gets its ``Job`` first, then JSON-only arguments:

    @tracked_job(job_type="analyze_companies", max_attempts=3, base_delay=5)
    def analyze_companies(job: Job, company_ids: list[str]) -> dict[str, Any]:
        add_items(job, [("company", cid) for cid in company_ids])      # sets total_count
        for item in job.items.exclude(status__in=ITEM_TERMINAL):
            start_item(item)
            try:
                result = analyze(item.subject_id)
            except Exception as exc:                  # one bad company does not fail the job
                fail_item(item, f"{type(exc).__name__}: {exc}")
            else:
                complete_item(item, result)
        return {"analyzed": job.done_count}

    job = enqueue(analyze_companies, client=client, campaign=campaign, created_by=user,
                  company_ids=[str(c) for c in ids])   # returns the queued Job immediately

When the function returns, the wrapper calls ``complete_job``: all items succeeded -> ``succeeded``,
a mix -> ``partial``, all failed -> ``failed``. An exception retries with exponential backoff
(status back to ``queued``, ``attempts`` counted) and, after ``max_attempts`` runs, ends ``failed``.

Counts are changed under a row lock (``SELECT ... FOR UPDATE`` on the job), so concurrent workers
finishing items never lose an update. Status changes are checked against the legal transitions
(``JOB_TRANSITIONS``); ``Job.save`` enforces them too.

Rules: task functions must be idempotent (ADR 0005; a redelivered task skips items already done),
and arguments and results must be plain JSON values. Pass ids, never model instances.
"""

from __future__ import annotations

import functools
import json
import logging
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import async_task, schedule

from apps.accounts.models import User
from apps.campaigns.models import Campaign, Client

from .logging import redact_text, reset_job_id, set_job_id
from .models import (
    ITEM_TERMINAL,
    ITEM_TRANSITIONS,
    JOB_TERMINAL,
    InvalidTransitionError,
    Job,
    JobItem,
    JobItemStatus,
    JobStatus,
)

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 2000

JobFunc = Callable[..., dict[str, Any] | None]

__all__ = [
    "ITEM_TERMINAL",
    "JOB_TERMINAL",
    "InvalidTransitionError",
    "JobStateError",
    "add_items",
    "complete_item",
    "complete_job",
    "create_job",
    "enqueue",
    "ensure_json",
    "fail_item",
    "fail_job",
    "record_progress",
    "requeue_job",
    "set_total",
    "start_item",
    "start_job",
    "tracked_job",
]


class JobStateError(ValueError):
    """The job or item is not in a state where this call makes sense (finished, unprocessed
    items left, counts that exceed the total)."""


# ------------------------------------------------------------------ registry of job types

JOB_TYPES: dict[str, str] = {}


def register_job_type(job_type: str, path: str) -> None:
    """Remember that ``job_type`` is run by the function at ``path`` (done by ``tracked_job``)."""
    existing = JOB_TYPES.get(job_type)
    if existing is not None and existing != path:
        raise ValueError(f"Job type {job_type!r} is already registered by {existing}.")
    JOB_TYPES[job_type] = path


def ensure_json(value: object, what: str) -> None:
    """Raise TypeError unless `value` survives a JSON round trip unchanged."""
    try:
        round_tripped = json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{what} must be JSON serializable: {exc}") from exc
    if round_tripped != value:
        raise TypeError(f"{what} must be plain JSON values (tuples, sets, etc. are not allowed)")


def _clip(text: str) -> str:
    """Secrets scrubbed, bounded length: error text is stored and shown."""
    return redact_text(text)[:MAX_ERROR_LENGTH]


# ------------------------------------------------------------------ locking


@contextmanager
def _locked_job(job: Job) -> Iterator[Job]:
    """Lock the job's row for the duration of the block, then refresh ``job`` from the result."""
    with transaction.atomic():
        yield Job.objects.select_for_update().get(pk=job.pk)
    job.refresh_from_db()


def _require_open(job: Job) -> None:
    if job.is_finished:
        raise JobStateError(f"Job {job.pk} is already {job.status}.")


# ------------------------------------------------------------------ create and run


def create_job(
    job_type: str,
    *,
    client: Client,
    campaign: Campaign | None = None,
    created_by: User | None = None,
) -> Job:
    """Create a queued job. ``job_type`` must be registered by a ``@tracked_job`` function.

    Refuses an archived client or campaign (archived campaigns take no new jobs). A campaign of
    another client raises ``TenantMismatchError``.
    """
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job type {job_type!r}; register it with @tracked_job.")
    if client.is_archived:
        raise ValidationError("Cannot start a job for an archived client.")
    if campaign is not None and campaign.is_archived:
        raise ValidationError("Cannot start a job for an archived campaign.")
    return Job.objects.create(
        type=job_type, client=client, campaign=campaign, created_by=created_by
    )


def start_job(job: Job) -> None:
    """queued -> running (also accepted while running: a redelivered task). Counts the attempt."""
    with _locked_job(job) as locked:
        _require_open(locked)
        locked.status = JobStatus.RUNNING
        locked.attempts += 1
        locked.error_summary = ""
        if locked.started_at is None:
            locked.started_at = timezone.now()
        locked.save(
            update_fields=["status", "attempts", "error_summary", "started_at", "updated_at"]
        )


def requeue_job(job: Job, error: str) -> None:
    """running -> queued: the run failed and a retry is scheduled. ``error`` is kept for display."""
    with _locked_job(job) as locked:
        locked.status = JobStatus.QUEUED
        locked.error_summary = _clip(error)
        locked.save(update_fields=["status", "error_summary", "updated_at"])


def fail_job(job: Job, error: str) -> None:
    """Final failure of the whole run (not of single items). Counts are left as they are."""
    with _locked_job(job) as locked:
        _require_open(locked)
        locked.status = JobStatus.FAILED
        locked.error_summary = _clip(error)
        locked.finished_at = timezone.now()
        locked.save(update_fields=["status", "error_summary", "finished_at", "updated_at"])


def set_total(job: Job, total: int) -> None:
    """Say how many items there are. It cannot go below what is already processed."""
    with _locked_job(job) as locked:
        _require_open(locked)
        if total < locked.processed_count:
            raise JobStateError(
                f"total_count {total} is below the {locked.processed_count} already processed."
            )
        locked.total_count = total
        locked.save(update_fields=["total_count", "updated_at"])


def record_progress(job: Job, *, done: int = 0, failed: int = 0) -> None:
    """Add to the counts of a job without item rows (a counted run). Atomic under the row lock.

    For per-company results use ``start_item`` / ``complete_item`` / ``fail_item`` instead.
    """
    if done < 0 or failed < 0:
        raise ValueError("done and failed must not be negative.")
    with _locked_job(job) as locked:
        _require_open(locked)
        _bump(locked, done, failed)


def _bump(locked: Job, done: int, failed: int) -> None:
    if locked.processed_count + done + failed > locked.total_count:
        raise JobStateError(
            f"Counts would exceed total_count ({locked.processed_count + done + failed} > "
            f"{locked.total_count}); call set_total first."
        )
    locked.done_count += done
    locked.failed_count += failed
    locked.save(update_fields=["done_count", "failed_count", "updated_at"])


def complete_job(job: Job, error_summary: str = "") -> None:
    """Finish a running job from its counts: no failures -> ``succeeded``, some -> ``partial``,
    only failures -> ``failed``. Raises ``JobStateError`` if items are still unprocessed.

    A failed item never fails the job record: it is what makes it ``partial``.
    """
    with _locked_job(job) as locked:
        _require_open(locked)
        open_items = locked.items.exclude(status__in=ITEM_TERMINAL).count()
        if open_items or locked.processed_count < locked.total_count:
            left = open_items or locked.total_count - locked.processed_count
            raise JobStateError(f"Job {locked.pk} still has {left} unprocessed item(s).")
        if locked.failed_count == 0:
            locked.status = JobStatus.SUCCEEDED
        elif locked.done_count == 0:
            locked.status = JobStatus.FAILED
        else:
            locked.status = JobStatus.PARTIAL
        # Item errors were scrubbed when stored; scrubbing the joined text again would mangle it.
        locked.error_summary = (
            _clip(error_summary)
            if error_summary
            else _summarize_failures(locked)[:MAX_ERROR_LENGTH]
        )
        locked.finished_at = timezone.now()
        locked.save(update_fields=["status", "error_summary", "finished_at", "updated_at"])


def _summarize_failures(locked: Job) -> str:
    if not locked.failed_count:
        return ""
    errors = [
        error
        for error in locked.items.filter(status=JobItemStatus.FAILED)
        .order_by("finished_at")
        .values_list("error", flat=True)[:3]
        if error
    ]
    head = f"{locked.failed_count} of {locked.total_count} item(s) failed"
    return f"{head}: " + "; ".join(errors) if errors else head


# ------------------------------------------------------------------ items


def add_items(job: Job, subjects: Iterable[tuple[str, uuid.UUID | str]]) -> list[JobItem]:
    """Add one queued item per ``(subject_type, subject_id)`` and raise ``total_count`` to match.

    A subject can appear once per job (``IntegrityError`` otherwise). Call it again for more.
    """
    with _locked_job(job) as locked:
        _require_open(locked)
        items = [
            JobItem(
                job=locked,
                client_id=locked.client_id,
                subject_type=subject_type,
                subject_id=uuid.UUID(str(subject_id)),
            )
            for subject_type, subject_id in subjects
        ]
        JobItem.objects.bulk_create(items)  # client_id is set above, save() is not needed
        locked.total_count += len(items)
        locked.save(update_fields=["total_count", "updated_at"])
    return items


@contextmanager
def _locked_item(item: JobItem) -> Iterator[tuple[Job, JobItem]]:
    """Lock the job first (the same order everywhere, so no deadlocks), then the item."""
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=item.job_id)
        yield job, JobItem.objects.select_for_update().get(pk=item.pk)
    item.refresh_from_db()


def start_item(item: JobItem) -> None:
    """queued -> running (running again is accepted: a redelivered task)."""
    with _locked_item(item) as (job, locked):
        _require_open(job)
        if locked.status == JobItemStatus.RUNNING:
            return
        locked.status = JobItemStatus.RUNNING
        locked.started_at = timezone.now()
        locked.save(update_fields=["status", "started_at", "updated_at"])


def _finish_item(item: JobItem, status: JobItemStatus, error: str, result: Any) -> None:
    with _locked_item(item) as (job, locked):
        _require_open(job)
        if status not in ITEM_TRANSITIONS.get(locked.status, frozenset()):
            raise InvalidTransitionError(f"Job item cannot go from {locked.status} to {status}.")
        locked.status = status
        locked.error = error
        locked.result = result
        locked.finished_at = timezone.now()
        if locked.started_at is None:
            locked.started_at = locked.finished_at
        locked.save(
            update_fields=["status", "error", "result", "started_at", "finished_at", "updated_at"]
        )
        if status == JobItemStatus.SUCCEEDED:
            _bump(job, 1, 0)
        else:
            _bump(job, 0, 1)


def complete_item(item: JobItem, result: Any = None) -> None:
    """Item succeeded: stores the optional JSON ``result`` and counts it in ``done_count``."""
    if result is not None:
        ensure_json(result, "Item result")
    _finish_item(item, JobItemStatus.SUCCEEDED, "", result)


def fail_item(item: JobItem, error: str) -> None:
    """Item failed: stores the error and counts it in ``failed_count``. The job carries on."""
    _finish_item(item, JobItemStatus.FAILED, _clip(error), None)


# ------------------------------------------------------------------ Django-Q2 glue


def backoff_delay(attempt: int, base_delay: float) -> timedelta:
    """Exponential backoff: base, 2x base, 4x base, ... for attempt 1, 2, 3, ..."""
    return timedelta(seconds=base_delay * 2 ** (attempt - 1))


def func_path(func: Callable[..., Any]) -> str:
    return f"{func.__module__}.{func.__qualname__}"


def enqueue(
    func: JobFunc,
    *args: Any,
    client: Client,
    campaign: Campaign | None = None,
    created_by: User | None = None,
    **kwargs: Any,
) -> Job:
    """Create a job and queue `func` (a `@tracked_job` function) for the worker.

    ``client``, ``campaign`` and ``created_by`` describe the job; every other argument goes to
    the task. Returns the job (queued, or already finished when Q_CLUSTER runs in sync mode).
    """
    job_type = getattr(func, "job_type", None)
    if job_type is None:
        raise TypeError(f"{func!r} is not a @tracked_job function.")
    ensure_json([list(args), kwargs], "Task arguments")
    job = create_job(job_type, client=client, campaign=campaign, created_by=created_by)
    task_id = async_task(func_path(func), str(job.pk), *args, **kwargs)
    # Save the task id without touching status: a sync-mode run has already finished the job.
    Job.objects.filter(pk=job.pk).update(queue_task_id=str(task_id or "")[:64])
    job.refresh_from_db()
    return job


def tracked_job(
    *, job_type: str | None = None, max_attempts: int = 3, base_delay: float = 5.0
) -> Callable[[JobFunc], JobFunc]:
    """Wrap `func(job, *args, **kwargs)` as a worker task `task(job_id, *args, **kwargs)`.

    Registers ``job_type`` (default: the function name). The wrapper starts the job, completes it
    from its counts when ``func`` returns, and on an exception either schedules a retry after an
    exponential backoff (status back to "queued") or, once `max_attempts` runs have been used,
    marks the job "failed" and re-raises so Django-Q2 records the failure too. Django-Q2's own
    retry is not used (`ack_failures` is on), so each run is counted exactly once. A task whose
    job is already finished (a redelivery) is skipped.
    """

    def decorator(func: JobFunc) -> JobFunc:
        name = job_type or func.__name__

        @functools.wraps(func)
        def task(job_id: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            job = Job.objects.get(pk=job_id)
            if job.is_finished:
                logger.warning("Job %s is already %s; skipping this delivery.", job_id, job.status)
                return None
            token = set_job_id(job_id)
            try:
                start_job(job)
                try:
                    result = func(job, *args, **kwargs)
                    if result is not None:
                        ensure_json(result, "Task result")
                    job.refresh_from_db()
                    complete_job(job)
                except Exception as exc:
                    job.refresh_from_db()
                    error = f"{type(exc).__name__}: {exc}"
                    if job.attempts < max_attempts:
                        delay = backoff_delay(job.attempts, base_delay)
                        requeue_job(job, error)
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
                    fail_job(job, error)
                    logger.error("Job %s failed permanently: %s", job_id, error)
                    raise
                return result
            finally:
                reset_job_id(token)

        task.job_type = name  # type: ignore[attr-defined]
        register_job_type(name, func_path(task))
        return task

    return decorator
