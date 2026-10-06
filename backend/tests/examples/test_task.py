"""Example: task layer (Django-Q2).

A task is a `@tracked_job` function that takes the `BackgroundJob` plus JSON arguments and
returns a JSON dict. Test it two ways:

* call `enqueue(...)` and assert on the job record: test settings set `Q_CLUSTER["sync"] = True`,
  so the task runs inline with no worker or broker;
* keep real logic in plain functions and unit test those directly.

See `tests/test_jobs.py` for the retry, backoff and failure cases.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.accounts.models import User
from apps.core.jobs import enqueue, tracked_job
from apps.core.models import BackgroundJob, JobStatus
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def deactivate_stale_users(emails: list[str]) -> int:
    """Plain business function: deactivate users by email, return how many changed."""
    return User.objects.filter(email__in=emails, is_active=True).update(is_active=False)


@tracked_job(max_attempts=1)
def deactivate_users_task(job: BackgroundJob, emails: list[str]) -> dict[str, Any]:
    """Thin task wrapper: idempotent, JSON in, JSON out."""
    return {"deactivated": deactivate_stale_users(emails)}


def test_function_updates_only_requested_rows():
    stale = make_user(email="stale@example.com")
    keep = make_user(email="keep@example.com")

    assert deactivate_stale_users(["stale@example.com"]) == 1

    stale.refresh_from_db()
    keep.refresh_from_db()
    assert stale.is_active is False
    assert keep.is_active is True


def test_function_is_idempotent():
    make_user(email="stale@example.com")
    assert deactivate_stale_users(["stale@example.com"]) == 1
    assert deactivate_stale_users(["stale@example.com"]) == 0


def test_enqueued_task_runs_and_records_success():
    make_user(email="stale@example.com")

    job = enqueue(deactivate_users_task, emails=["stale@example.com"])

    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert User.objects.get(email="stale@example.com").is_active is False
