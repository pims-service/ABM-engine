"""Smoke tasks that prove the worker round trip, retry/backoff and the failed state.

To add a task: write a `@tracked_job` function in the owning app's `tasks.py` (the module is found
automatically at startup so its job type is registered), then call
`apps.core.jobs.enqueue(my_task, client=..., ...)` from a view, service or management command.
"""

from __future__ import annotations

from typing import Any

from .jobs import record_progress, set_total, tracked_job
from .models import Job


@tracked_job(job_type="ping", max_attempts=1)
def ping(job: Job, message: str = "pong") -> dict[str, Any]:
    """Round trip: web/CLI enqueues, worker runs, counts and status are recorded."""
    set_total(job, 2)
    record_progress(job, done=1)
    record_progress(job, done=1)
    return {"ok": True, "echo": message}


@tracked_job(job_type="flaky", max_attempts=3, base_delay=2)
def flaky(job: Job, succeed_on_attempt: int = 3) -> dict[str, Any]:
    """Retry/backoff example: raises until the given attempt number is reached.

    With the default it fails twice (retries after 2s, then 4s) and succeeds on the third run.
    With `succeed_on_attempt` above `max_attempts` it ends in the visible "failed" state.
    """
    if job.attempts < succeed_on_attempt:
        raise RuntimeError(f"simulated failure on attempt {job.attempts}")
    return {"ok": True, "attempts": job.attempts}
