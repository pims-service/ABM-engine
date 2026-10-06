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
from django.contrib.auth.models import User

from apps.core.jobs import enqueue, tracked_job
from apps.core.models import BackgroundJob, JobStatus
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def deactivate_stale_users(usernames: list[str]) -> int:
    """Plain business function: deactivate users by username, return how many changed."""
    return User.objects.filter(username__in=usernames, is_active=True).update(is_active=False)


@tracked_job(max_attempts=1)
def deactivate_users_task(job: BackgroundJob, usernames: list[str]) -> dict[str, Any]:
    """Thin task wrapper: idempotent, JSON in, JSON out."""
    return {"deactivated": deactivate_stale_users(usernames)}


def test_function_updates_only_requested_rows():
    stale = make_user(username="stale")
    keep = make_user(username="keep")

    assert deactivate_stale_users(["stale"]) == 1

    stale.refresh_from_db()
    keep.refresh_from_db()
    assert stale.is_active is False
    assert keep.is_active is True


def test_function_is_idempotent():
    make_user(username="stale")
    assert deactivate_stale_users(["stale"]) == 1
    assert deactivate_stale_users(["stale"]) == 0


def test_enqueued_task_runs_and_records_success():
    make_user(username="stale")

    job = enqueue(deactivate_users_task, usernames=["stale"])

    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert User.objects.get(username="stale").is_active is False
