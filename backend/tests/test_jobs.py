"""Tests for the job-status helpers, smoke tasks and the enqueue command (sync Q_CLUSTER)."""

from __future__ import annotations

import ast
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django_q.models import Schedule, Success
from django_q.tasks import async_task

from apps.core.jobs import (
    backoff_delay,
    create_job,
    enqueue,
    ensure_json,
    mark_failed,
    tracked_job,
    update_progress,
)
from apps.core.models import BackgroundJob, JobStatus
from apps.core.tasks import flaky, ping

pytestmark = pytest.mark.django_db


@tracked_job(max_attempts=1)
def _failing_task(job, message="x"):
    raise ValueError("boom")


def test_q_cluster_uses_orm_broker_and_sync_in_tests():
    from django.conf import settings

    assert settings.Q_CLUSTER["orm"] == "default"
    assert settings.Q_CLUSTER["sync"] is True
    assert settings.Q_CLUSTER["retry"] > settings.Q_CLUSTER["timeout"]


def test_ping_round_trip():
    job = enqueue(ping, message="hello")

    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert job.progress == 100
    assert job.attempts == 1
    assert job.finished_at is not None
    result = Success.objects.get().result
    assert result == {"ok": True, "echo": "hello"}


def test_failure_schedules_retry_with_backoff():
    job = enqueue(flaky, succeed_on_attempt=3)

    job.refresh_from_db()
    assert job.status == JobStatus.RETRYING
    assert job.attempts == 1
    assert "simulated failure" in job.error
    retry = Schedule.objects.get()
    assert retry.func == "apps.core.tasks.flaky"
    assert timedelta(seconds=1) < retry.next_run - job.updated_at <= timedelta(seconds=2.5)


def test_retried_run_succeeds_when_it_stops_failing():
    job = enqueue(flaky, succeed_on_attempt=2)
    # The scheduler would run the scheduled retry; do the same by hand.
    retry = Schedule.objects.get()
    # Schedule stores args and kwargs as their repr; the scheduler parses them with literal_eval.
    async_task(retry.func, *ast.literal_eval(retry.args), **ast.literal_eval(retry.kwargs))

    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert job.error == ""


def test_exhausted_attempts_end_in_failed_state():
    job = create_job("fail")
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
    assert "simulated failure" in job.error
    assert job.finished_at is not None


def test_backoff_doubles():
    assert backoff_delay(1, 2) == timedelta(seconds=2)
    assert backoff_delay(2, 2) == timedelta(seconds=4)
    assert backoff_delay(3, 2) == timedelta(seconds=8)


def test_progress_is_clamped():
    job = create_job("x")
    update_progress(job, 150)
    assert BackgroundJob.objects.get(pk=job.pk).progress == 100
    update_progress(job, -5)
    assert BackgroundJob.objects.get(pk=job.pk).progress == 0


def test_mark_failed_truncates_long_errors():
    job = create_job("x")
    mark_failed(job, "e" * 5000)
    assert len(job.error) == 2000


@pytest.mark.parametrize("value", [{"a": (1, 2)}, {"a": {1, 2}}, {"a": object()}, {1: "x"}])
def test_ensure_json_rejects_non_json(value):
    with pytest.raises(TypeError):
        ensure_json(value, "thing")


def test_enqueue_rejects_non_json_arguments():
    with pytest.raises(TypeError, match="JSON"):
        enqueue(ping, message={"bad": {1, 2}})
    assert BackgroundJob.objects.count() == 0


def test_task_result_must_be_json():
    from apps.core.jobs import tracked_job

    @tracked_job(max_attempts=1)
    def bad(job):
        return {"x": {1, 2}}

    job = create_job("bad")
    with pytest.raises(TypeError):
        bad(str(job.pk))
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED


def test_str():
    assert str(create_job("x")) == "x [queued]"


def test_command_ping():
    out = StringIO()
    call_command("enqueue_smoke_task", "ping", "--wait", "5", stdout=out)
    assert "status=succeeded" in out.getvalue()


def test_command_flaky_without_wait_just_enqueues():
    out = StringIO()
    call_command("enqueue_smoke_task", "flaky", stdout=out)
    assert "Enqueued flaky job" in out.getvalue()
    assert BackgroundJob.objects.get().status == JobStatus.RETRYING


def test_command_reports_a_failed_job(monkeypatch):
    monkeypatch.setattr("apps.core.management.commands.enqueue_smoke_task.ping", _failing_task)
    # Sync mode re-raises the task's error; on a real worker the command reports "failed".
    with pytest.raises(ValueError, match="boom"):
        call_command("enqueue_smoke_task", "ping", stdout=StringIO())
    assert BackgroundJob.objects.get().status == JobStatus.FAILED


def test_command_fail_variant_times_out_waiting_for_retry():
    with pytest.raises(CommandError, match="did not finish"):
        call_command("enqueue_smoke_task", "fail", "--wait", "1", stdout=StringIO())
