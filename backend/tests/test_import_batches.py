"""ImportBatch / ImportRow models and the import services (issue #56)."""

from __future__ import annotations

import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from apps.core.base import ImmutableRecordError, TenantMismatchError
from apps.core.models import InvalidTransitionError
from apps.imports import services
from apps.imports.models import ImportBatch, ImportBatchStatus, ImportRow, ImportRowOutcome
from apps.imports.services import BatchStateError
from tests.factories import make_campaign, make_company, make_user
from tests.factories_core import make_job
from tests.factories_imports import make_import_batch, make_import_row

pytestmark = pytest.mark.django_db

SHA = "a" * 64


def _csv_batch(campaign=None, total=0, **kw):
    return services.create_batch(
        campaign or make_campaign(),
        "csv",
        original_filename=kw.pop("original_filename", "q3.csv"),
        file_size=10,
        file_sha256=SHA,
        total_count=total,
        **kw,
    )


# ------------------------------------------------------------------ create_batch


def test_create_batch_copies_client_and_records_file_facts() -> None:
    user = make_user()
    campaign = make_campaign()
    batch = services.create_batch(
        campaign,
        "csv",
        user=user,
        original_filename="C:\\Users\\me\\leads.csv",
        file_size=99,
        file_sha256=SHA.upper(),
        column_mapping={"Company": "name"},
    )
    assert batch.client_id == campaign.client_id
    assert batch.status == ImportBatchStatus.PENDING
    assert batch.original_filename == "leads.csv"
    assert batch.file_sha256 == SHA
    assert batch.column_mapping == {"Company": "name"}
    assert batch.created_by == user
    assert batch.progress_percent == 0
    assert str(batch) == "csv import [pending]"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source": "fax"},
        {"source": "csv"},  # no sha
        {"source": "manual", "file_sha256": "xyz"},
        {"source": "manual", "file_size": -1},
        {"source": "manual", "total_count": -1},
        {"source": "manual", "column_mapping": {"a": object()}},
        {"source": "manual", "column_mapping": {"a": "x" * 20000}},
    ],
)
def test_create_batch_validates(kwargs) -> None:
    with pytest.raises(ValidationError):
        services.create_batch(make_campaign(), **kwargs)


def test_archived_campaign_takes_no_import() -> None:
    campaign = make_campaign()
    campaign.archive()
    with pytest.raises(ValidationError):
        services.create_batch(campaign, "manual")


def test_database_requires_sha_for_csv() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_import_batch(source="csv")


def test_job_must_belong_to_the_same_client() -> None:
    campaign = make_campaign()
    other_job = make_job(client=make_campaign().client)
    with pytest.raises(TenantMismatchError):
        services.create_batch(campaign, "manual", job=other_job)
    other_campaign_job = make_job(
        client=campaign.client, campaign=make_campaign(client=campaign.client)
    )
    with pytest.raises(TenantMismatchError):
        services.create_batch(campaign, "manual", job=other_campaign_job)
    good = make_job(client=campaign.client, campaign=campaign)
    batch = services.create_batch(campaign, "manual")
    services.attach_job(batch, good)
    assert batch.job == good
    with pytest.raises(TenantMismatchError):
        services.attach_job(batch, other_job)


# ------------------------------------------------------------------ transitions


def test_status_transitions_are_enforced_on_save() -> None:
    batch = make_import_batch()
    batch.status = ImportBatchStatus.COMPLETED  # pending -> completed is not legal
    with pytest.raises(InvalidTransitionError):
        batch.save()
    batch.refresh_from_db()
    services.start_batch(batch)
    services.start_batch(batch)  # redelivery is fine
    assert batch.status == ImportBatchStatus.PROCESSING
    assert batch.started_at
    services.cancel_batch(batch, "no longer needed")
    stored = ImportBatch.objects.get(pk=batch.pk)
    assert stored.status == ImportBatchStatus.CANCELLED
    assert stored.finished_at
    with pytest.raises(BatchStateError):
        services.start_batch(batch)
    batch.status = ImportBatchStatus.PROCESSING
    with pytest.raises(InvalidTransitionError):
        batch.save()


def test_set_total_cannot_go_below_processed() -> None:
    batch = make_import_batch(total_count=0)
    services.set_total(batch, 2)
    services.record_row_outcome(batch, 1, "failed", error_code="x")
    with pytest.raises(BatchStateError):
        services.set_total(batch, 0)
    with pytest.raises(ValueError, match="negative"):
        services.set_total(batch, -1)


# ------------------------------------------------------------------ rows


def test_record_row_outcome_counts_and_is_idempotent() -> None:
    batch = make_import_batch(total_count=3)
    company = make_company(campaign=batch.campaign)
    first = services.record_row_outcome(batch, 1, "created", raw={"name": "A"}, company=company)
    assert batch.status == ImportBatchStatus.PROCESSING
    assert batch.created_count == 1
    again = services.record_row_outcome(batch, 1, "failed", error_code="x")
    assert again.pk == first.pk
    assert again.outcome == "created"
    assert batch.processed_count == 1
    assert batch.progress_percent == 33
    services.record_row_outcome(batch, 2, "skipped")
    services.record_row_outcome(batch, 3, "failed", error_code="name_required", error_message="m")
    batch.refresh_from_db()
    assert (batch.skipped_count, batch.failed_count) == (1, 1)
    with pytest.raises(BatchStateError):
        services.record_row_outcome(batch, 4, "skipped")


@pytest.mark.parametrize(
    ("kwargs", "exc"),
    [
        ({"row_number": 1, "outcome": "nope"}, ValueError),
        ({"row_number": 0, "outcome": "skipped"}, ValueError),
        ({"row_number": 1, "outcome": "created"}, ValueError),  # no company
        ({"row_number": 1, "outcome": "failed"}, ValueError),  # no code
    ],
)
def test_record_row_outcome_validates(kwargs, exc) -> None:
    batch = make_import_batch(total_count=1)
    with pytest.raises(exc):
        services.record_row_outcome(batch, **kwargs)


def test_row_company_must_match_batch_campaign() -> None:
    batch = make_import_batch(total_count=1)
    foreign = make_company(campaign=make_campaign())
    with pytest.raises(TenantMismatchError):
        services.record_row_outcome(batch, 1, "duplicate", company=foreign)
    sibling = make_company(campaign=make_campaign(client=batch.client))
    with pytest.raises(TenantMismatchError):
        services.record_row_outcome(batch, 1, "duplicate", company=sibling)
    batch.refresh_from_db()
    assert batch.processed_count == 0
    assert not ImportRow.objects.exists()


def test_rows_are_append_only_and_unique_per_batch() -> None:
    row = make_import_row(row_number=1)
    row.name = "changed"
    with pytest.raises(ImmutableRecordError):
        row.save()
    with pytest.raises(ImmutableRecordError):
        row.delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_import_row(batch=row.batch, row_number=1)
    assert str(row) == "row 1 [skipped]"


def test_row_checks_in_the_database() -> None:
    batch = make_import_batch()
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRow.objects.bulk_create(
            [ImportRow(batch=batch, client_id=batch.client_id, row_number=1, outcome="created")]
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        make_import_row(batch=batch, outcome="failed")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_import_row(batch=batch, row_number=0)


def test_raw_data_is_scrubbed_on_save_even_without_the_service() -> None:
    row = make_import_row(raw_data={"name": "A", "password": "hunter2"})  # pragma: allowlist secret
    row.refresh_from_db()
    assert "hunter2" not in str(row.raw_data)
    assert row.raw_data["name"] == "A"


# ------------------------------------------------------------------ finalize


def test_finalize_statuses() -> None:
    def run(outcomes: list[str]) -> ImportBatch:
        batch = make_import_batch(total_count=len(outcomes))
        for n, outcome in enumerate(outcomes, start=1):
            if outcome in ("created", "duplicate", "restored"):
                company = make_company(campaign=batch.campaign)
                services.record_row_outcome(batch, n, outcome, company=company)
            else:
                services.record_row_outcome(batch, n, outcome, error_code="bad_row")
        services.finalize_batch(batch)
        return batch

    ok = run(["created", "duplicate", "restored", "skipped"])
    assert ok.status == ImportBatchStatus.COMPLETED
    assert ok.error_summary == ""
    assert ok.finished_at
    assert ok.progress_percent == 100
    partial = run(["created", "failed"])
    assert partial.status == ImportBatchStatus.PARTIAL
    assert partial.error_summary == "1 of 2 row(s) failed: row 2: bad_row"
    assert run(["failed", "failed"]).status == ImportBatchStatus.FAILED
    empty = make_import_batch(total_count=0)
    services.finalize_batch(empty)
    assert empty.status == ImportBatchStatus.COMPLETED
    assert empty.progress_percent == 100
    with pytest.raises(BatchStateError):
        services.finalize_batch(empty)


def test_finalize_needs_every_row_and_scrubs_summary() -> None:
    batch = make_import_batch(total_count=2)
    services.record_row_outcome(batch, 1, "skipped")
    with pytest.raises(BatchStateError):
        services.finalize_batch(batch)
    services.fail_batch(batch, "could not parse; token=abc123xyz")  # pragma: allowlist secret
    assert batch.status == ImportBatchStatus.FAILED
    assert "abc123xyz" not in batch.error_summary
    assert batch.skipped_count == 1


# ------------------------------------------------------------------ process_row


def test_process_row_runs_the_shared_pipeline() -> None:
    campaign = make_campaign()
    batch = services.create_batch(campaign, "provider", total_count=6)

    created = services.process_row(
        batch, 1, {"name": "Acme", "website": "https://www.acme.com", "country": "sa"}
    )
    assert created.outcome == ImportRowOutcome.CREATED
    assert (created.domain, created.country) == ("acme.com", "SA")
    company = created.company
    assert company is not None
    assert company.input_source == "provider"
    assert company.campaign == campaign

    dup = services.process_row(batch, 2, {"name": "Acme again", "website": "acme.com"})
    assert dup.outcome == ImportRowOutcome.DUPLICATE
    assert dup.company == company

    company.archive()
    restored = services.process_row(batch, 3, {"name": "Acme", "website": "acme.com"})
    assert restored.outcome == ImportRowOutcome.RESTORED
    company.refresh_from_db()
    assert not company.is_archived

    bad = services.process_row(batch, 4, {"name": "", "website": "nope", "api_key": "k-123"})
    assert bad.outcome == ImportRowOutcome.FAILED
    assert bad.error_code == "name_required"
    assert "website" in bad.error_message
    assert bad.company is None
    assert "k-123" not in str(bad.raw_data)

    again = services.process_row(batch, 4, {"name": "Fixed"})
    assert again.pk == bad.pk

    services.process_row(batch, 5, {"name": "No Domain"})
    services.process_row(batch, 6, {"name": "No Domain"})  # same-name warning, still created
    batch.refresh_from_db()
    assert (
        batch.created_count,
        batch.duplicate_count,
        batch.restored_count,
        batch.failed_count,
    ) == (3, 1, 1, 1)
    services.finalize_batch(batch)
    assert batch.status == ImportBatchStatus.PARTIAL


def test_process_row_turns_a_company_refusal_into_a_failed_row() -> None:
    campaign = make_campaign()
    batch = services.create_batch(campaign, "manual", total_count=1)
    campaign.archive()
    row = services.process_row(batch, 1, {"name": "Acme"})
    assert row.outcome == ImportRowOutcome.FAILED
    assert row.error_code == services.COMPANY_REJECTED
    assert row.name == "Acme"
    assert row.company is None
    assert not row.company_id
    assert ImportBatch.objects.get(pk=batch.pk).failed_count == 1


def test_process_row_rolls_back_the_company_when_recording_fails() -> None:
    from apps.companies.models import Company

    batch = services.create_batch(make_campaign(), "manual", total_count=0)
    with pytest.raises(BatchStateError):
        services.process_row(batch, 1, {"name": "Acme", "website": "acme.com"})
    assert not Company.objects.filter(campaign=batch.campaign).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor == "sqlite", reason="needs row locks and real concurrency")
def test_concurrent_rows_never_lose_a_count() -> None:
    batch = make_import_batch(total_count=8)
    errors: list[BaseException] = []

    def work(start: int) -> None:
        try:
            for n in range(start, start + 4):
                services.record_row_outcome(ImportBatch.objects.get(pk=batch.pk), n, "skipped")
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(s,)) for s in (1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    batch.refresh_from_db()
    assert batch.skipped_count == 8
    assert ImportRow.objects.filter(batch=batch).count() == 8


def test_querysets_scope_by_client() -> None:
    a, b = make_import_batch(), make_import_batch()
    assert list(ImportBatch.objects.for_client(a.client)) == [a]
    assert set(ImportBatch.objects.active()) == {a, b}
    row = make_import_row(batch=a)
    assert list(ImportRow.objects.for_client(a.client)) == [row]
    assert not ImportRow.objects.for_client(b.client).exists()
