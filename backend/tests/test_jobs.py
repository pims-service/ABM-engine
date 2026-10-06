"""Tests for the Django-Q2 glue: enqueue, smoke tasks, retry/backoff, failed state, the command.

Q_CLUSTER runs in sync mode under the test settings, so `enqueue` runs the task inline.
Job state machine and counting rules are in `tests/test_job_service.py`.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django_q.models import Schedule, Success
from django_q.tasks import async_task

from apps.core.jobs import (
    JOB_TYPES,
    add_items,
    backoff_delay,
    complete_item,
    create_job,
    enqueue,
    ensure_json,
    fail_item,
    fail_job,
    start_item,
    tracked_job,
)
from apps.core.logging import get_job_id
from apps.core.models import ITEM_TERMINAL, Job, JobStatus
from apps.core.tasks import flaky, ping
from tests.factories import make_campaign, make_client, make_user

pytestmark = pytest.mark.django_db


@tracked_job(job_type="always_boom", max_attempts=1)
def _failing_task(job: Job, message: str = "x") -> None:
    raise ValueError("boom")


@tracked_job(job_type="analyze_test", max_attempts=1)
def _analyze(job: Job, subjects: list[str]) -> dict[str, Any]:
    """Per-subject task: the subject named 'bad' fails, the rest succeed."""
    add_items(job, [("company", s) for s in subjects])
    for item in job.items.exclude(status__in=ITEM_TERMINAL):
        start_item(item)
        assert get_job_id() == str(job.pk)
        if str(item.subject_id) == BAD:
            fail_item(item, "provider timeout token=abc123")  # pragma: allowlist secret
        else:
            complete_item(item, {"score": 1})
    job.refresh_from_db()
    return {"seen": job.processed_count}


GOOD1, GOOD2, BAD = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)


def test_q_cluster_uses_orm_broker_and_sync_in_tests():
    from django.conf import settings

    assert settings.Q_CLUSTER["orm"] == "default"
    assert settings.Q_CLUSTER["sync"] is True
    assert settings.Q_CLUSTER["retry"] > settings.Q_CLUSTER["timeout"]


def test_ping_round_trip():
    client = make_client()
    user = make_user()
    campaign = make_campaign(client=client)
    job = enqueue(ping, client=client, campaign=campaign, created_by=user, message="hello")

    assert job.status == JobStatus.SUCCEEDED
    assert (job.total_count, job.done_count, job.failed_count) == (2, 2, 0)
    assert job.progress_percent == 100
    assert job.attempts == 1
    assert job.type == "ping"
    assert (job.client, job.campaign, job.created_by) == (client, campaign, user)
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.queue_task_id
    assert Success.objects.get().result == {"ok": True, "echo": "hello"}


def test_enqueue_requires_a_tracked_function():
    with pytest.raises(TypeError, match="tracked_job"):
        enqueue(lambda job: None, client=make_client())
    assert Job.objects.count() == 0


def test_per_company_task_ends_partial_and_failed_item_does_not_fail_job():
    job = enqueue(_analyze, client=make_client(), subjects=[GOOD1, GOOD2, BAD])

    assert job.status == JobStatus.PARTIAL
    assert (job.total_count, job.done_count, job.failed_count) == (3, 2, 1)
    assert job.finished_at is not None
    assert job.error_summary.startswith("1 of 3 item(s) failed")
    assert "abc123" not in job.error_summary  # secrets scrubbed from stored errors
    assert job.error_summary.endswith("token=[REDACTED]")
    assert job.items.get(subject_id=BAD).status == "failed"
    assert job.items.get(subject_id=GOOD1).result == {"score": 1}


def test_task_where_every_item_fails_ends_failed():
    job = enqueue(_analyze, client=make_client(), subjects=[BAD])
    assert job.status == JobStatus.FAILED
    assert (job.done_count, job.failed_count) == (0, 1)


def test_task_where_every_item_succeeds_ends_succeeded():
    job = enqueue(_analyze, client=make_client(), subjects=[GOOD1, GOOD2])
    assert job.status == JobStatus.SUCCEEDED
    assert job.error_summary == ""


def test_job_id_context_is_cleared_after_the_task():
    enqueue(ping, client=make_client())
    assert get_job_id() is None


def test_failure_schedules_retry_with_backoff():
    job = enqueue(flaky, client=make_client(), succeed_on_attempt=3)

    assert job.status == JobStatus.QUEUED  # waiting for its retry
    assert job.attempts == 1
    assert job.started_at is not None
    assert job.finished_at is None
    assert "simulated failure" in job.error_summary
    retry = Schedule.objects.get()
    assert retry.func == "apps.core.tasks.flaky"
    assert timedelta(seconds=1) < retry.next_run - job.updated_at <= timedelta(seconds=2.5)


def test_retried_run_succeeds_when_it_stops_failing():
    job = enqueue(flaky, client=make_client(), succeed_on_attempt=2)
    # The scheduler would run the scheduled retry; do the same by hand.
    retry = Schedule.objects.get()
    # Schedule stores args and kwargs as their repr; the scheduler parses them with literal_eval.
    async_task(retry.func, *ast.literal_eval(retry.args), **ast.literal_eval(retry.kwargs))

    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert job.error_summary == ""


def test_exhausted_attempts_end_in_failed_state():
    job = create_job("flaky", client=make_client())
    for run in range(1, 4):
        if run < 3:
            # Runs 1 and 2 are caught and rescheduled.
            async_task("apps.core.tasks.flaky", str(job.pk), succeed_on_attempt=99)
        else:
            # The final run marks the job failed and re-raises so django-q records a Failure.
            with pytest.raises(RuntimeError):
                async_task("apps.core.tasks.flaky", str(job.pk), succeed_on_attempt=99)
    assert Schedule.objects.count() == 2

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.attempts == 3
    assert "simulated failure" in job.error_summary
    assert job.finished_at is not None


def test_redelivered_task_for_a_finished_job_is_skipped():
    job = enqueue(ping, client=make_client())
    assert job.status == JobStatus.SUCCEEDED

    assert async_task("apps.core.tasks.ping", str(job.pk))  # delivered again
    job.refresh_from_db()
    assert job.attempts == 1
    assert job.status == JobStatus.SUCCEEDED


def test_failed_job_is_not_restarted_by_a_stray_delivery():
    job = create_job("ping", client=make_client())
    fail_job(job, "cancelled")
    async_task("apps.core.tasks.ping", str(job.pk))
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.attempts == 0


def test_task_result_must_be_json():
    @tracked_job(job_type="bad_result", max_attempts=1)
    def bad(job):
        return {"x": {1, 2}}

    job = create_job("bad_result", client=make_client())
    with pytest.raises(TypeError):
        bad(str(job.pk))
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED


def test_job_types_are_registered_by_name():
    assert {"ping", "flaky"} <= set(JOB_TYPES)
    assert JOB_TYPES["ping"] == "apps.core.tasks.ping"
    with pytest.raises(ValueError, match="already registered"):

        @tracked_job(job_type="ping")
        def other(job):
            return None


def test_backoff_doubles():
    assert backoff_delay(1, 2) == timedelta(seconds=2)
    assert backoff_delay(2, 2) == timedelta(seconds=4)
    assert backoff_delay(3, 2) == timedelta(seconds=8)


@pytest.mark.parametrize("value", [{"a": (1, 2)}, {"a": {1, 2}}, {"a": object()}, {1: "x"}])
def test_ensure_json_rejects_non_json(value):
    with pytest.raises(TypeError):
        ensure_json(value, "thing")


def test_enqueue_rejects_non_json_arguments():
    with pytest.raises(TypeError, match="JSON"):
        enqueue(ping, client=make_client(), message={"bad": {1, 2}})
    assert Job.objects.count() == 0


def test_command_ping_uses_a_smoke_client():
    out = StringIO()
    call_command("enqueue_smoke_task", "ping", "--wait", "5", stdout=out)
    assert "status=succeeded" in out.getvalue()
    assert Job.objects.get().client.name == "Smoke test (system)"


def test_command_with_named_client():
    client = make_client(name="Acme")
    call_command("enqueue_smoke_task", "ping", "--client", "Acme", stdout=StringIO())
    assert Job.objects.get().client == client
    with pytest.raises(CommandError, match="No active client"):
        call_command("enqueue_smoke_task", "ping", "--client", "Nope", stdout=StringIO())


def test_command_flaky_without_wait_just_enqueues():
    out = StringIO()
    call_command("enqueue_smoke_task", "flaky", stdout=out)
    assert "Enqueued flaky job" in out.getvalue()
    assert Job.objects.get().status == JobStatus.QUEUED


def test_command_reports_a_failed_job(monkeypatch):
    monkeypatch.setattr("apps.core.management.commands.enqueue_smoke_task.ping", _failing_task)
    # Sync mode re-raises the task's error; on a real worker the command reports "failed".
    with pytest.raises(ValueError, match="boom"):
        call_command("enqueue_smoke_task", "ping", stdout=StringIO())
    assert Job.objects.get().status == JobStatus.FAILED


def test_command_fail_variant_times_out_waiting_for_retry():
    with pytest.raises(CommandError, match="did not finish"):
        call_command("enqueue_smoke_task", "fail", "--wait", "1", stdout=StringIO())
