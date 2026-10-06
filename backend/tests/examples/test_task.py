"""Example: task layer.

Django-Q2 is not installed yet (separate issue). Keep task bodies as plain functions that take
simple arguments and return a result, so they test synchronously with no broker. When Django-Q2
lands, enqueue with `async_task("path.to.func", ...)`, test the function directly, and set
`Q_CLUSTER = {"sync": True}` in test settings for end-to-end checks.
"""

import pytest
from django.contrib.auth.models import User

from tests.factories import make_user

pytestmark = pytest.mark.django_db


def deactivate_stale_users(usernames: list[str]) -> int:
    """Stand-in task: deactivate users by username, return how many were changed."""
    return User.objects.filter(username__in=usernames, is_active=True).update(is_active=False)


def test_task_updates_only_requested_rows():
    stale = make_user(username="stale")
    keep = make_user(username="keep")

    assert deactivate_stale_users(["stale"]) == 1

    stale.refresh_from_db()
    keep.refresh_from_db()
    assert stale.is_active is False
    assert keep.is_active is True


def test_task_is_idempotent():
    make_user(username="stale")
    assert deactivate_stale_users(["stale"]) == 1
    assert deactivate_stale_users(["stale"]) == 0
