"""Job and JobItem: state machine, counts, constraints and the service functions (issue #44)."""

from __future__ import annotations

import threading
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction

from apps.campaigns.services import archive_campaign, archive_client
from apps.core.base import TenantMismatchError
from apps.core.jobs import (
    JOB_TERMINAL,
    JobStateError,
    add_items,
    complete_item,
    complete_job,
    create_job,
    fail_item,
    fail_job,
    record_progress,
    requeue_job,
    set_total,
    start_item,
    start_job,
)
from apps.core.models import (
    JOB_TRANSITIONS,
    InvalidTransitionError,
    Job,
    JobItem,
    JobItemStatus,
    JobStatus,
)
from tests.factories import make_campaign, make_client, make_user
from tests.factories_core import make_job, make_job_item

pytestmark = pytest.mark.django_db


def uid() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------ creation


def test_create_job_defaults():
    client, user = make_client(), make_user()
    campaign = make_campaign(client=client)
    job = create_job("ping", client=client, campaign=campaign, created_by=user)

    assert job.status == JobStatus.QUEUED
    assert (job.total_count, job.done_count, job.failed_count, job.attempts) == (0, 0, 0, 0)
    assert job.started_at is None
    assert job.finished_at is None
    assert job.progress_percent == 0
    assert str(job) == "ping [queued]"
    assert list(Job.objects.for_client(client)) == [job]
    assert not Job.objects.for_client(make_client()).exists()


def test_job_type_must_be_registered():
    with pytest.raises(ValueError, match="Unknown job type"):
        create_job("made_up", client=make_client())


def test_campaign_of_another_client_is_rejected():
    campaign = make_campaign()
    with pytest.raises(TenantMismatchError):
        create_job("ping", client=make_client(), campaign=campaign)


def test_archived_client_or_campaign_takes_no_new_jobs():
    campaign = make_campaign()
    archive_campaign(campaign)
    with pytest.raises(ValidationError, match="archived campaign"):
        create_job("ping", client=campaign.client, campaign=campaign)
    client = make_client()
    client.archive()
    with pytest.raises(ValidationError, match="archived client"):
        create_job("ping", client=client)


# ------------------------------------------------------------------ state machine

ALL = [s.value for s in JobStatus]


@pytest.mark.parametrize("old", ALL)
@pytest.mark.parametrize("new", ALL)
def test_every_status_pair_follows_the_transition_table(old, new):
    """Walk the whole matrix through Job.save: legal moves work, the rest raise."""
    job = make_job()
    Job.objects.filter(pk=job.pk).update(
        status=old,
        finished_at="2026-01-01T00:00:00Z" if old in JOB_TERMINAL else None,
    )
    job.refresh_from_db()
    job.status = new
    job.finished_at = "2026-01-02T00:00:00Z" if new in JOB_TERMINAL else None
    if old == new or new in JOB_TRANSITIONS[old]:
        job.save()
        job.refresh_from_db()
        assert job.status == new
    else:
        with pytest.raises(InvalidTransitionError):
            job.save()


def test_terminal_states_have_no_exits():
    for status in JOB_TERMINAL:
        assert JOB_TRANSITIONS[status] == frozenset()


def test_service_functions_follow_the_happy_path():
    job = create_job("ping", client=make_client())
    start_job(job)
    assert job.status == JobStatus.RUNNING
    assert job.attempts == 1
    first_start = job.started_at
    assert first_start is not None
    complete_job(job)
    assert job.status == JobStatus.SUCCEEDED
    assert job.finished_at is not None


def test_succeeded_job_cannot_run_again():
    job = create_job("ping", client=make_client())
    start_job(job)
    complete_job(job)
    with pytest.raises(JobStateError, match="already succeeded"):
        start_job(job)
    with pytest.raises(JobStateError):
        fail_job(job, "late")
    with pytest.raises(JobStateError):
        complete_job(job)


def test_requeue_keeps_attempts_and_started_at_then_runs_again():
    job = create_job("ping", client=make_client())
    start_job(job)
    started = job.started_at
    requeue_job(job, "RuntimeError: flaky")
    assert job.status == JobStatus.QUEUED
    assert job.error_summary == "RuntimeError: flaky"
    assert job.finished_at is None
    start_job(job)
    assert job.attempts == 2
    assert job.started_at == started
    assert job.error_summary == ""


def test_requeue_after_a_finished_job_is_refused():
    job = create_job("ping", client=make_client())
    start_job(job)
    complete_job(job)
    job.status = JobStatus.QUEUED
    with pytest.raises(InvalidTransitionError):
        job.save()


def test_queued_job_can_fail_without_running():
    job = create_job("ping", client=make_client())
    fail_job(job, "cancelled by operator")
    assert job.status == JobStatus.FAILED
    assert job.finished_at is not None


def test_queued_job_cannot_jump_to_succeeded():
    job = create_job("ping", client=make_client())
    job.status = JobStatus.SUCCEEDED
    with pytest.raises(InvalidTransitionError):
        job.save()


def test_error_summary_is_scrubbed_and_clipped():
    job = create_job("ping", client=make_client())
    fail_job(job, "bad password=hunter2 " + "x" * 5000)  # pragma: allowlist secret
    assert "hunter2" not in job.error_summary
    assert len(job.error_summary) <= 2000


# ------------------------------------------------------------------ completion outcomes


def run_items(outcomes):
    """Job with one item per outcome ("ok" or "bad"), all processed, still running."""
    job = create_job("ping", client=make_client())
    start_job(job)
    items = add_items(job, [("company", uid()) for _ in outcomes])
    for item, outcome in zip(items, outcomes, strict=True):
        item.refresh_from_db()
        start_item(item)
        if outcome == "ok":
            complete_item(item)
        else:
            fail_item(item, f"error {item.pk}")
    return job


@pytest.mark.parametrize(
    ("outcomes", "status", "done", "failed"),
    [
        ([], JobStatus.SUCCEEDED, 0, 0),
        (["ok", "ok"], JobStatus.SUCCEEDED, 2, 0),
        (["ok", "bad", "ok"], JobStatus.PARTIAL, 2, 1),
        (["bad", "bad"], JobStatus.FAILED, 0, 2),
    ],
)
def test_completion_status_comes_from_the_counts(outcomes, status, done, failed):
    job = run_items(outcomes)
    complete_job(job)
    assert job.status == status
    assert (job.done_count, job.failed_count) == (done, failed)
    assert job.total_count == len(outcomes)
    assert job.progress_percent == 100


def test_partial_summary_lists_item_errors():
    job = run_items(["ok", "bad"])
    complete_job(job)
    assert job.error_summary.startswith("1 of 2 item(s) failed: error ")


def test_explicit_error_summary_wins():
    job = run_items(["ok", "bad"])
    complete_job(job, "provider was down")
    assert job.error_summary == "provider was down"


def test_cannot_complete_with_unprocessed_items():
    job = create_job("ping", client=make_client())
    start_job(job)
    add_items(job, [("company", uid()), ("company", uid())])
    with pytest.raises(JobStateError, match="unprocessed"):
        complete_job(job)
    assert job.status == JobStatus.RUNNING


def test_cannot_complete_when_counts_fall_short_of_total():
    job = create_job("ping", client=make_client())
    start_job(job)
    set_total(job, 3)
    record_progress(job, done=2)
    with pytest.raises(JobStateError, match="unprocessed"):
        complete_job(job)


# ------------------------------------------------------------------ counts


def test_record_progress_and_set_total():
    job = create_job("ping", client=make_client())
    start_job(job)
    set_total(job, 4)
    record_progress(job, done=1)
    record_progress(job, done=1, failed=1)
    assert (job.done_count, job.failed_count, job.progress_percent) == (2, 1, 75)
    with pytest.raises(JobStateError, match="exceed total_count"):
        record_progress(job, done=2)
    with pytest.raises(JobStateError, match="below"):
        set_total(job, 2)
    with pytest.raises(ValueError, match="negative"):
        record_progress(job, done=-1)
    assert (job.done_count, job.failed_count) == (2, 1)


def test_counts_cannot_exceed_total_in_the_database():
    job = make_job()
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.filter(pk=job.pk).update(done_count=1)  # total_count is 0


def test_finished_at_must_match_status_in_the_database():
    job = make_job()
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.filter(pk=job.pk).update(finished_at="2026-01-01T00:00:00Z")
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.filter(pk=job.pk).update(status=JobStatus.FAILED)


def test_status_check_constraint():
    job = make_job()
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.filter(pk=job.pk).update(status="paused")


def test_progress_percent_of_a_finished_job_without_items_is_100():
    job = create_job("ping", client=make_client())
    start_job(job)
    complete_job(job)
    assert job.total_count == 0
    assert job.progress_percent == 100


# ------------------------------------------------------------------ items


def test_add_items_sets_client_and_total():
    job = create_job("ping", client=make_client())
    start_job(job)
    items = add_items(job, [("company", uid()), ("company", uid())])
    job.refresh_from_db()
    assert job.total_count == 2
    assert {i.client_id for i in items} == {job.client_id}
    assert job.items.count() == 2
    assert list(JobItem.objects.for_client(job.client)) == list(job.items.all())
    more = add_items(job, [("company", uid())])
    job.refresh_from_db()
    assert job.total_count == 3
    assert len(more) == 1


def test_a_subject_appears_once_per_job():
    job = create_job("ping", client=make_client())
    start_job(job)
    subject = uid()
    add_items(job, [("company", subject)])
    with pytest.raises(IntegrityError), transaction.atomic():
        add_items(job, [("company", subject)])
    job.refresh_from_db()
    assert job.total_count == 1
    add_items(job, [("contact", subject)])  # another subject type is fine


def test_item_lifecycle_updates_job_counts():
    job = create_job("ping", client=make_client())
    start_job(job)
    ok, bad = add_items(job, [("company", uid()), ("company", uid())])
    ok.refresh_from_db()
    bad.refresh_from_db()

    start_item(ok)
    assert ok.status == JobItemStatus.RUNNING
    assert ok.started_at is not None
    start_item(ok)  # redelivery: no change
    complete_item(ok, {"fit": "strong"})
    fail_item(bad, "no website")

    assert (ok.status, ok.result, ok.finished_at is not None) == (
        "succeeded",
        {"fit": "strong"},
        True,
    )
    assert (bad.status, bad.error) == ("failed", "no website")
    assert bad.started_at == bad.finished_at  # went straight from queued to failed
    job.refresh_from_db()
    assert (job.done_count, job.failed_count) == (1, 1)
    assert str(ok).startswith("company:")


def test_finished_items_are_final():
    job = create_job("ping", client=make_client())
    start_job(job)
    (item,) = add_items(job, [("company", uid())])
    item.refresh_from_db()
    complete_item(item)
    with pytest.raises(InvalidTransitionError):
        fail_item(item, "again")
    item.status = JobItemStatus.RUNNING
    with pytest.raises(InvalidTransitionError):
        item.save()
    job.refresh_from_db()
    assert (job.done_count, job.failed_count) == (1, 0)


def test_item_result_must_be_json():
    job = create_job("ping", client=make_client())
    start_job(job)
    (item,) = add_items(job, [("company", uid())])
    with pytest.raises(TypeError):
        complete_item(item, {"x": {1}})


def test_items_cannot_change_a_finished_job():
    job = create_job("ping", client=make_client())
    start_job(job)
    (item,) = add_items(job, [("company", uid())])
    fail_job(job, "aborted")
    with pytest.raises(JobStateError):
        complete_item(item)
    with pytest.raises(JobStateError):
        add_items(job, [("company", uid())])
    with pytest.raises(JobStateError):
        start_item(item)


def test_item_client_comes_from_the_job_and_cannot_disagree():
    job = make_job()
    item = make_job_item(job=job)
    assert item.client_id == job.client_id
    with pytest.raises(TenantMismatchError):
        JobItem(
            job=job, client=make_client(), subject_type="company", subject_id=uuid.uuid4()
        ).save()


def test_item_status_and_finished_constraints():
    item = make_job_item()
    with pytest.raises(IntegrityError), transaction.atomic():
        JobItem.objects.filter(pk=item.pk).update(status="weird")
    with pytest.raises(IntegrityError), transaction.atomic():
        JobItem.objects.filter(pk=item.pk).update(status=JobItemStatus.SUCCEEDED)


# ------------------------------------------------------------------ archive guards


def test_cannot_archive_client_or_campaign_with_active_jobs():
    campaign = make_campaign()
    job = create_job("ping", client=campaign.client, campaign=campaign)
    with pytest.raises(ValidationError, match="queued or running jobs"):
        archive_campaign(campaign)
    with pytest.raises(ValidationError, match="queued or running jobs"):
        archive_client(campaign.client)
    start_job(job)
    complete_job(job)
    archive_campaign(campaign)
    archive_client(campaign.client)
    assert campaign.is_archived
    assert campaign.client.is_archived


# ------------------------------------------------------------------ locking


@pytest.mark.skipif(connection.vendor != "postgresql", reason="row locks need PostgreSQL")
@pytest.mark.django_db(transaction=True)
def test_concurrent_progress_does_not_lose_updates():
    job = create_job("ping", client=make_client())
    start_job(job)
    set_total(job, 40)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(10):
                record_progress(Job.objects.get(pk=job.pk), done=1)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    job.refresh_from_db()
    assert job.done_count == 40
