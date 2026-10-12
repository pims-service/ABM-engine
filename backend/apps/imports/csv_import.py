"""CSV bulk import: accept an upload, then process its rows in the background (issue #62).

Two halves, both built on the pure parser (``csvparse``, #61) and the batch services (#56):

**Accepting** (``preview_csv``, ``submit_csv_import``; called by the API, request thread)

1. ``summarize`` reads the whole file once (constant memory) and rejects it before any work is
   queued: too large, too many rows, binary or undecodable, no header, no name column, bad
   mapping, no data rows. The encoding is decided by decoding, never by the declared content type.
2. In one transaction: a duplicate guard (the same SHA-256 into the same campaign while a batch is
   pending or processing is refused, ``DuplicateImportError``), the ``ImportBatch`` (source
   ``csv``, ``total_count`` known), the stored file (``uploads``), the ``core.Job`` and the
   Django-Q2 task ``process_csv_import(job_id, batch_id=...)``. The queue is the same Postgres
   database, so the task row commits together with the batch.

**Processing** (``run_csv_import``; the ``@tracked_job`` task in ``tasks.py``, worker thread)

* Streams rows with ``iter_rows``; each row goes through ``process_row`` (validation, dedupe,
  weak flags, company creation, outcome recorded in one transaction). A row that cannot be parsed
  is recorded as ``failed`` with the parser's code. One bad row never stops the batch.
* Idempotent and resumable: rows already recorded are skipped (``process_row`` is idempotent per
  ``(batch, row_number)`` anyway), so a redelivered task or a retry after a worker crash carries
  on where the last one stopped and never duplicates a company.
* Every ``settings.IMPORT_CHUNK_SIZE`` rows the job counters are brought in line with the batch
  counters (absolute values under a row lock, so a repeated run never double counts) and the
  batch is checked for cancellation. Cancelling stops the run at the next chunk edge.
* At the end ``finalize_batch`` sets completed / partial / failed, one audit entry records the
  result, and the stored file is deleted. A whole-file failure (file gone, undecodable midway)
  ends the batch ``failed`` with a reason; rows already recorded stay.

Logs carry ids and counts only: never file names, cell values or company names.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import IO, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django_q.tasks import async_task

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.core import jobs
from apps.core.audit import record_change, snapshot
from apps.core.models import AuditAction, Job

from . import uploads
from .csvparse import (
    DEFAULT_LIMITS,
    FIELDS,
    WARN_NO_DATA_ROWS,
    CsvFileError,
    CsvLimits,
    Mapping,
    ParsedRow,
    ParseSummary,
    iter_rows,
    preview,
    suggest_mapping,
    summarize,
)
from .models import ImportBatch, ImportBatchStatus, ImportRow, ImportRowOutcome, ImportSource
from .schema import InputErrors, validate_company_input
from .services import (
    INTERNAL_ERROR,
    BatchStateError,
    attach_job,
    cancel_batch,
    create_batch,
    fail_batch,
    finalize_batch,
    process_row,
    record_row_outcome,
    start_batch,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "process_csv_import"
TASK_PATH = "apps.imports.tasks.process_csv_import"
MAX_ATTEMPTS = 3
PREVIEW_ROWS = 10
ROW_INTERNAL_MESSAGE = "An unexpected error stopped this row. It was not imported."

BATCH_AUDIT_FIELDS = (
    "campaign",
    "source",
    "status",
    "original_filename",
    "file_size",
    "file_sha256",
    "total_count",
    "created_count",
    "duplicate_count",
    "restored_count",
    "skipped_count",
    "failed_count",
    "error_summary",
)


def csv_limits() -> CsvLimits:
    """The ceilings applied to an upload (``csvparse.DEFAULT_LIMITS``: 10 MiB, 5000 rows)."""
    return DEFAULT_LIMITS


def chunk_size() -> int:
    return max(1, int(settings.IMPORT_CHUNK_SIZE))


class DuplicateImportError(Exception):
    """The same file is already being imported into this campaign (the API answers 409)."""

    def __init__(self, batch: ImportBatch) -> None:
        self.batch = batch
        super().__init__(f"Import batch {batch.pk} of the same file is still {batch.status}.")


class BatchNotCancellableError(Exception):
    """The batch already ended and cannot be cancelled (the API answers 409)."""

    def __init__(self, batch: ImportBatch) -> None:
        self.batch = batch
        super().__init__(f"Import batch {batch.pk} is already {batch.status}.")


# ------------------------------------------------------------------ accepting an upload


def parse_mapping(data: Any) -> Mapping | None:
    """A ``{header: field}`` JSON object from the client as a ``Mapping`` (``None`` = suggest)."""
    if data is None:
        return None
    if not isinstance(data, MappingABC) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValidationError({"mapping": "Must be a JSON object of column name to field name."})
    return Mapping.from_dict(dict(data))


def _rewind(upload: IO[bytes]) -> None:
    upload.seek(0)


def _check_whole_file(
    upload: IO[bytes], mapping: Mapping | None, limits: CsvLimits
) -> ParseSummary:
    _rewind(upload)
    summary = summarize(upload, mapping, limits)
    if summary.total_rows == 0:
        raise CsvFileError(WARN_NO_DATA_ROWS)
    return summary


@dataclass(frozen=True, slots=True)
class PreviewResult:
    summary: ParseSummary
    headers: list[str]
    mapping: Mapping
    suggested: Mapping
    rows: list[ParsedRow]
    row_errors: dict[int, InputErrors]
    limits: CsvLimits


def preview_csv(
    upload: IO[bytes], mapping: Mapping | None = None, *, rows: int = PREVIEW_ROWS
) -> PreviewResult:
    """Validate the whole file, then show its first rows. Stores nothing, changes nothing.

    ``mapping`` (optional) is applied to the rows shown; the suggestion made from the headers is
    always returned too. Raises ``CsvFileError`` / ``MappingError`` for a file that could not be
    imported. Each shown row carries its parser errors and, for a parsed row, the company
    validation errors the import would record.
    """
    limits = csv_limits()
    summary = _check_whole_file(upload, mapping, limits)
    _rewind(upload)
    shown = preview(upload, mapping, limits, count=rows)
    errors: dict[int, InputErrors] = {}
    for row in shown.rows:
        if row.ok:
            checked = validate_company_input(_company_payload(row))
            if isinstance(checked, InputErrors):
                errors[row.row_number] = checked
    return PreviewResult(
        summary=summary,
        headers=shown.headers,
        mapping=shown.mapping,
        suggested=suggest_mapping(shown.headers),
        rows=shown.rows,
        row_errors=errors,
        limits=limits,
    )


def submit_csv_import(
    campaign: Campaign,
    user: User,
    upload: IO[bytes],
    *,
    filename: str,
    mapping: Mapping | None = None,
) -> tuple[ImportBatch, Job]:
    """Validate the file, create the batch and job, store the file and queue the task.

    Raises ``CsvFileError`` / ``MappingError`` (the file cannot be imported),
    ``DuplicateImportError`` (same file already pending/processing in this campaign) and Django
    ``ValidationError`` (archived campaign or client). Nothing is left behind on failure. In sync
    mode (tests) the task has already run when this returns.
    """
    from . import tasks  # noqa: F401  (registers the job type, whatever the import order)

    summary = _check_whole_file(upload, mapping, csv_limits())
    used = summary.mapping
    if used is None:  # a finished summary always has its mapping
        raise RuntimeError("The parser returned no mapping.")
    sha = summary.file_sha256
    stored = False
    batch: ImportBatch | None = None
    try:
        with transaction.atomic():
            # Serialise uploads into one campaign so two identical concurrent requests cannot
            # both pass the guard.
            locked = (
                Campaign.objects.select_for_update().select_related("client").get(pk=campaign.pk)
            )
            existing = (
                ImportBatch.objects.active()
                .filter(campaign=locked, source=ImportSource.CSV, file_sha256=sha)
                .first()
            )
            if existing is not None:
                raise DuplicateImportError(existing)
            batch = create_batch(
                locked,
                ImportSource.CSV,
                user=user,
                original_filename=filename,
                file_size=summary.file_size,
                file_sha256=sha,
                column_mapping=used.to_dict(),
                total_count=summary.total_rows,
            )
            uploads.save_upload(batch, upload)
            stored = True
            job = jobs.create_job(JOB_TYPE, client=locked.client, campaign=locked, created_by=user)
            attach_job(batch, job)
            jobs.set_total(job, summary.total_rows)
            record_change(
                AuditAction.CREATE,
                "import_batch",
                batch.pk,
                actor=user,
                client=locked.client,
                before=None,
                after=snapshot(batch, BATCH_AUDIT_FIELDS),
            )
            task_id = async_task(TASK_PATH, str(job.pk), batch_id=str(batch.pk))
            # Not through the job's status: a sync-mode run has already finished it.
            Job.objects.filter(pk=job.pk).update(queue_task_id=str(task_id or "")[:64])
    except BaseException:
        if stored and batch is not None:
            uploads.delete_upload(batch)
        raise
    if batch is None:  # pragma: no cover - the block above always sets it or raises
        raise RuntimeError("The import batch was not created.")
    batch.refresh_from_db()
    job.refresh_from_db()
    logger.info(
        "CSV import %s accepted for campaign %s: %s row(s), job %s.",
        batch.pk,
        campaign.pk,
        batch.total_count,
        job.pk,
    )
    return batch, job


def cancel_csv_import(batch: ImportBatch, user: User | None) -> ImportBatch:
    """Cancel a pending or processing batch (audited). Idempotent for a cancelled batch; any other
    finished batch raises ``BatchNotCancellableError``. The stored file is deleted at once and the
    worker stops at its next chunk edge. Rows already recorded (and their companies) stay."""
    batch.refresh_from_db()
    if batch.status == ImportBatchStatus.CANCELLED:
        return batch
    if batch.is_finished:
        raise BatchNotCancellableError(batch)
    before = snapshot(batch, BATCH_AUDIT_FIELDS)
    try:
        cancel_batch(batch, "Cancelled by the user.")
    except BatchStateError as exc:  # finished between the check and the lock
        batch.refresh_from_db()
        if batch.status == ImportBatchStatus.CANCELLED:
            return batch
        raise BatchNotCancellableError(batch) from exc
    record_change(
        AuditAction.UPDATE,
        "import_batch",
        batch.pk,
        actor=user,
        client=batch.client,
        before=before,
        after=snapshot(batch, BATCH_AUDIT_FIELDS),
    )
    uploads.delete_upload(batch)
    return batch


# ------------------------------------------------------------------ processing (worker)


def _company_payload(row: ParsedRow) -> dict[str, Any]:
    """What ``process_row`` receives: every column as submitted (it ends in ``raw_data``), with the
    four company fields set from the mapping only, so a column the user did not map to a field
    never feeds it by accident."""
    payload: dict[str, Any] = {k: v for k, v in row.raw.items() if k not in FIELDS}
    payload.update({field: row.mapped.get(field, "") for field in FIELDS})
    return payload


def _sync_job(
    job: Job, batch: ImportBatch, *, total: int | None = None, extra_failed: int = 0
) -> None:
    """Make the job's counters equal the batch's (absolute values under a row lock, so running it
    twice or after a crash never double counts)."""
    done = batch.created_count + batch.duplicate_count + batch.restored_count + batch.skipped_count
    failed = batch.failed_count + extra_failed
    with transaction.atomic():
        locked = Job.objects.select_for_update().get(pk=job.pk)
        if locked.is_finished:
            return
        locked.total_count = max(total if total is not None else batch.total_count, done + failed)
        locked.done_count = done
        locked.failed_count = failed
        locked.save(update_fields=["total_count", "done_count", "failed_count", "updated_at"])
    job.refresh_from_db()


def _audit_finish(batch: ImportBatch, before: dict[str, Any]) -> None:
    record_change(
        AuditAction.UPDATE,
        "import_batch",
        batch.pk,
        actor=None,
        client=batch.client,
        before=before,
        after=snapshot(batch, BATCH_AUDIT_FIELDS),
    )


def _record_unparsed(batch: ImportBatch, row: ParsedRow) -> None:
    issue = row.errors[0]
    record_row_outcome(
        batch,
        row.row_number,
        ImportRowOutcome.FAILED,
        raw=row.raw,
        error_code=issue.code,
        error_message=issue.message,
    )


def _handle_row(batch: ImportBatch, row: ParsedRow, user: User | None) -> None:
    if not row.ok:
        _record_unparsed(batch, row)
        return
    try:
        process_row(batch, row.row_number, _company_payload(row), user=user)
    except BatchStateError:
        raise
    except Exception as exc:  # one broken row must not stop the batch
        logger.warning(
            "Import batch %s row %s failed unexpectedly: %s",
            batch.pk,
            row.row_number,
            type(exc).__name__,
        )
        record_row_outcome(
            batch,
            row.row_number,
            ImportRowOutcome.FAILED,
            raw=row.raw,
            error_code=INTERNAL_ERROR,
            error_message=ROW_INTERNAL_MESSAGE,
        )


def _fail_whole_file(job: Job, batch: ImportBatch, error: CsvFileError | str) -> None:
    reason = str(error) if isinstance(error, CsvFileError) else error
    batch.refresh_from_db()
    if not batch.is_finished:
        fail_batch(batch, reason)
    batch.refresh_from_db()
    # The job must end failed too: one extra failed item makes ``complete_job`` say so.
    _sync_job(job, batch, total=batch.processed_count + 1, extra_failed=1)


def _is_finished(batch: ImportBatch) -> bool:
    """``batch.is_finished`` as a call, so a type checker does not carry an earlier check over a
    ``refresh_from_db``."""
    return batch.is_finished


def _is_cancelled(batch: ImportBatch) -> bool:
    return batch.status == ImportBatchStatus.CANCELLED


def _result(batch: ImportBatch) -> dict[str, Any]:
    return {
        "batch_id": str(batch.pk),
        "status": batch.status,
        "total": batch.total_count,
        "created": batch.created_count,
        "duplicate": batch.duplicate_count,
        "restored": batch.restored_count,
        "skipped": batch.skipped_count,
        "failed": batch.failed_count,
    }


def run_csv_import(job: Job, batch_id: str) -> dict[str, Any]:
    """The body of the ``process_csv_import`` task. Safe to run again for the same batch."""
    batch = ImportBatch.objects.select_related("campaign", "client").get(pk=batch_id)
    if batch.client_id != job.client_id or batch.source != ImportSource.CSV:
        raise ValueError(f"Job {job.pk} does not belong to CSV import batch {batch.pk}.")
    if batch.is_finished:  # redelivered after the end, or cancelled before it started
        uploads.delete_upload(batch)
        _sync_job(job, batch, total=batch.processed_count)
        return _result(batch)
    before = snapshot(batch, BATCH_AUDIT_FIELDS)
    try:
        _process(job, batch)
    except Exception as exc:
        job.refresh_from_db()
        if job.attempts >= MAX_ATTEMPTS:  # the wrapper is about to give up on this job
            batch.refresh_from_db()
            if not batch.is_finished:
                fail_batch(
                    batch, f"The import stopped after repeated errors ({type(exc).__name__})."
                )
                _audit_finish(batch, before)
            uploads.delete_upload(batch)
        raise
    batch.refresh_from_db()
    if _is_finished(batch):
        uploads.delete_upload(batch)
        if _is_cancelled(batch):
            _sync_job(job, batch, total=batch.processed_count)
        _audit_finish(batch, before)
    return _result(batch)


def _process(job: Job, batch: ImportBatch) -> None:
    user = batch.created_by
    start_batch(batch)
    _sync_job(job, batch)
    try:
        stream = uploads.open_upload(batch)
    except FileNotFoundError:
        _fail_whole_file(job, batch, "The uploaded file is no longer available. Upload it again.")
        return
    with stream:
        try:
            rows = iter_rows(stream, Mapping.from_dict(batch.column_mapping), csv_limits())
        except CsvFileError as exc:
            _fail_whole_file(job, batch, exc)
            return
        with rows:
            done = set(ImportRow.objects.filter(batch=batch).values_list("row_number", flat=True))
            if not _read_and_record(job, batch, rows, done, user):
                return  # cancelled, or ended as failed
    try:
        finalize_batch(batch)
    except BatchStateError:
        batch.refresh_from_db()
        if not batch.is_finished:
            _fail_whole_file(job, batch, "The file changed while it was being imported.")
            return
    batch.refresh_from_db()
    if batch.status != ImportBatchStatus.CANCELLED:
        _sync_job(job, batch)


def _read_and_record(
    job: Job, batch: ImportBatch, rows: Any, done: set[int], user: User | None
) -> bool:
    """Process every row not yet recorded. False when the run must stop (cancelled / failed)."""
    since_checkpoint = 0
    step = chunk_size()
    try:
        for row in rows:
            if row.row_number in done:
                continue
            try:
                _handle_row(batch, row, user)
            except BatchStateError:
                batch.refresh_from_db()
                if batch.is_finished:  # cancelled while this chunk ran
                    return False
                _fail_whole_file(job, batch, "The file changed while it was being imported.")
                return False
            since_checkpoint += 1
            if since_checkpoint >= step:
                since_checkpoint = 0
                batch.refresh_from_db()
                _sync_job(job, batch)
                if batch.is_finished:
                    return False
    except CsvFileError as exc:  # undecodable or too long, found midway
        _fail_whole_file(job, batch, exc)
        return False
    batch.refresh_from_db()
    return not batch.is_finished


__all__ = [
    "JOB_TYPE",
    "MAX_ATTEMPTS",
    "BatchNotCancellableError",
    "DuplicateImportError",
    "PreviewResult",
    "cancel_csv_import",
    "chunk_size",
    "csv_limits",
    "parse_mapping",
    "preview_csv",
    "run_csv_import",
    "submit_csv_import",
]
