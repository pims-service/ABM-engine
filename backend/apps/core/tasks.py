"""Smoke tasks that prove the worker round trip, retry/backoff and the failed state.

To add a task: write a `@tracked_job` function in the owning app's `tasks.py`, then call
`apps.core.jobs.enqueue(my_task, ...)` from a view, service or management command.
"""

from __future__ import annotations

from typing import Any

from .jobs import tracked_job, update_progress
from .models import BackgroundJob


@tracked_job(max_attempts=1)
def ping(job: BackgroundJob, message: str = "pong") -> dict[str, Any]:
    """Round trip: web/CLI enqueues, worker runs, status is recorded."""
    update_progress(job, 50)
    return {"ok": True, "echo": message}


@tracked_job(max_attempts=3, base_delay=2)
def flaky(job: BackgroundJob, succeed_on_attempt: int = 3) -> dict[str, Any]:
    """Retry/backoff example: raises until the given attempt number is reached.

    With the default it fails twice (retries after 2s, then 4s) and succeeds on the third run.
    With `succeed_on_attempt` above `max_attempts` it ends in the visible "failed" state.
    """
    if job.attempts < succeed_on_attempt:
        raise RuntimeError(f"simulated failure on attempt {job.attempts}")
    return {"ok": True, "attempts": job.attempts}
