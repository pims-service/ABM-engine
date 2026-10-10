"""Write path for import batches (issue #56). The same calls serve manual entry, CSV upload and
provider import; permission checks belong to the API layer.

Typical run (a background task for a CSV, or a request for one manual company)::

    batch = create_batch(campaign, "csv", user=user, original_filename="q3.csv",
                         file_size=size, file_sha256=digest, column_mapping=mapping)
    set_total(batch, len(rows))                          # known after parsing
    for number, raw in enumerate(rows, start=1):         # row_number is 1-based
        process_row(batch, number, raw, user=user)       # validate, dedupe, create, record
    finalize_batch(batch)                                # completed / partial / failed

* ``process_row`` is the shared pipeline: ``validate_company_input`` then
  ``apps.companies.services.create_company``, and the outcome recorded in one transaction. A row
  that is already recorded is returned unchanged, so a redelivered task is safe (ADR 0005).
* ``record_row_outcome`` is the lower level: store one outcome and bump the matching counter
  under a row lock on the batch (``SELECT ... FOR UPDATE``), like ``apps.core.jobs`` does for
  jobs, so concurrent workers never lose a count.
* ``finalize_batch`` sets the final status from the counters: no failed rows is ``completed``,
  some failed and some not is ``partial``, all failed is ``failed``.
* ``fail_batch`` ends a batch whose run broke as a whole (an unreadable file); ``cancel_batch``
  stops one on request. Rows already recorded stay.

Error codes stored on failed rows are the ones of ``apps.imports.schema`` plus
``COMPANY_REJECTED`` and ``INTERNAL_ERROR`` below.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.companies.services import CompanyResult, create_company
from apps.core.logging import redact_text
from apps.core.models import InvalidTransitionError, Job

from .models import (
    BATCH_TRANSITIONS,
    OUTCOME_COUNTERS,
    ROW_COMPANY_OUTCOMES,
    ImportBatch,
    ImportBatchStatus,
    ImportRow,
    ImportRowOutcome,
    ImportSource,
)
from .schema import CompanyInput, InputErrors, validate_company_input

__all__ = [
    "COMPANY_REJECTED",
    "INTERNAL_ERROR",
    "BatchStateError",
    "attach_job",
    "cancel_batch",
    "create_batch",
    "fail_batch",
    "finalize_batch",
    "process_row",
    "record_row_outcome",
    "set_total",
    "start_batch",
]

COMPANY_REJECTED = "company_rejected"  # create_company refused the (valid) input
INTERNAL_ERROR = "internal_error"  # an unexpected exception while processing the row

MAX_ERROR_LENGTH = 2000
MAX_FILENAME_LENGTH = 255
MAX_MAPPING_BYTES = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BatchStateError(ValueError):
    """The batch is not in a state where this call makes sense (finished, rows left, counts
    that would exceed the total)."""


def _clip(text: str) -> str:
    """Secrets scrubbed, bounded length: error text is stored and shown."""
    return redact_text(text)[:MAX_ERROR_LENGTH]


@contextmanager
def _locked_batch(batch: ImportBatch) -> Iterator[ImportBatch]:
    """Lock the batch's row for the block, then refresh ``batch`` from the result."""
    with transaction.atomic():
        yield ImportBatch.objects.select_for_update().get(pk=batch.pk)
    batch.refresh_from_db()


def _require_open(batch: ImportBatch) -> None:
    if batch.is_finished:
        raise BatchStateError(f"Import batch {batch.pk} is already {batch.status}.")


def _move(batch: ImportBatch, status: str) -> None:
    """Change status (checked against the legal transitions by ``ImportBatch.save``)."""
    if status not in BATCH_TRANSITIONS[batch.status]:
        raise InvalidTransitionError(f"Import batch cannot go from {batch.status} to {status}.")
    batch.status = status


# ------------------------------------------------------------------ create


def create_batch(
    campaign: Campaign,
    source: str,
    *,
    user: User | None = None,
    original_filename: str = "",
    file_size: int | None = None,
    file_sha256: str = "",
    column_mapping: Mapping[str, Any] | None = None,
    total_count: int = 0,
    job: Job | None = None,
) -> ImportBatch:
    """Open a ``pending`` batch for ``campaign`` (its client is copied from it).

    ``source`` is ``manual``, ``csv`` or ``provider``. A CSV batch needs ``file_sha256`` (64 hex
    characters of the uploaded bytes, lower case); the file name is reduced to its base name.
    ``column_mapping`` is free-form JSON (at most 16 KiB). ``total_count`` may be given now (a
    manual entry has 1) or later with ``set_total``. An archived campaign takes no imports.
    """
    if source not in ImportSource.values:
        raise ValidationError({"source": f"Unknown import source {source!r}."})
    if campaign.is_archived:
        raise ValidationError("Cannot import into an archived campaign.")
    file_sha256 = file_sha256.strip().lower()
    if file_sha256 and not _SHA256.match(file_sha256):
        raise ValidationError({"file_sha256": "Must be 64 lower-case hex characters."})
    if source == ImportSource.CSV and not file_sha256:
        raise ValidationError({"file_sha256": "A CSV import needs the SHA-256 of the file."})
    if file_size is not None and file_size < 0:
        raise ValidationError({"file_size": "Must not be negative."})
    if total_count < 0:
        raise ValidationError({"total_count": "Must not be negative."})
    mapping = dict(column_mapping or {})
    try:
        encoded = json.dumps(mapping)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"column_mapping": f"Must be JSON serializable: {exc}"}) from exc
    if len(encoded.encode("utf-8")) > MAX_MAPPING_BYTES:
        raise ValidationError({"column_mapping": "Too large."})
    filename = re.split(r"[\\/]", original_filename.strip())[-1][:MAX_FILENAME_LENGTH]
    return ImportBatch.objects.create(
        campaign=campaign,
        source=source,
        created_by=user,
        original_filename=filename,
        file_size=file_size,
        file_sha256=file_sha256,
        column_mapping=json.loads(encoded),
        total_count=total_count,
        job=job,
    )


def attach_job(batch: ImportBatch, job: Job) -> None:
    """Link the background ``Job`` that processes this batch (same client and campaign)."""
    with _locked_batch(batch) as locked:
        _require_open(locked)
        locked.job = job
        locked.check_job()
        locked.save(update_fields=["job", "updated_at"])


def set_total(batch: ImportBatch, total: int) -> None:
    """Say how many rows there are. It cannot go below what is already processed."""
    if total < 0:
        raise ValueError("total must not be negative.")
    with _locked_batch(batch) as locked:
        _require_open(locked)
        if total < locked.processed_count:
            raise BatchStateError(
                f"total_count {total} is below the {locked.processed_count} already processed."
            )
        locked.total_count = total
        locked.save(update_fields=["total_count", "updated_at"])


def start_batch(batch: ImportBatch) -> None:
    """pending -> processing (accepted while processing: a redelivered task)."""
    with _locked_batch(batch) as locked:
        _require_open(locked)
        _start(locked)


def _start(locked: ImportBatch) -> None:
    if locked.status == ImportBatchStatus.PROCESSING:
        return
    _move(locked, ImportBatchStatus.PROCESSING)
    locked.started_at = timezone.now()
    locked.save(update_fields=["status", "started_at", "updated_at"])


# ------------------------------------------------------------------ rows


def record_row_outcome(
    batch: ImportBatch,
    row_number: int,
    outcome: str,
    *,
    raw: Any = None,
    company_input: CompanyInput | None = None,
    company: Company | None = None,
    error_code: str = "",
    error_message: str = "",
) -> ImportRow:
    """Store what happened to row ``row_number`` and count it, atomically.

    * The first call starts a pending batch.
    * ``created``, ``duplicate`` and ``restored`` need the ``company`` (new or existing, of this
      batch's campaign); ``failed`` needs an ``error_code``.
    * The row is written once. If ``row_number`` is already recorded (a redelivered task), the
      stored row is returned and nothing is counted again.
    * Counters may not exceed ``total_count``: call ``set_total`` first.

    ``raw`` is stored scrubbed and size-capped; ``company_input`` fills the normalized columns.
    """
    if outcome not in ImportRowOutcome.values:
        raise ValueError(f"Unknown row outcome {outcome!r}.")
    if row_number < 1:
        raise ValueError("row_number starts at 1.")
    if outcome in ROW_COMPANY_OUTCOMES and company is None:
        raise ValueError(f"A {outcome} row needs the company it refers to.")
    if outcome == ImportRowOutcome.FAILED and not error_code:
        raise ValueError("A failed row needs an error_code.")
    normalized = company_input.as_dict() if company_input else {}
    with _locked_batch(batch) as locked:
        existing = ImportRow.objects.filter(batch=locked, row_number=row_number).first()
        if existing is not None:
            return existing
        _require_open(locked)
        if locked.processed_count + 1 > locked.total_count:
            raise BatchStateError(
                f"Counts would exceed total_count ({locked.processed_count + 1} > "
                f"{locked.total_count}); call set_total first."
            )
        _start(locked)
        row = ImportRow.objects.create(
            batch=locked,
            row_number=row_number,
            raw_data=raw,
            name=normalized.get("name") or "",
            website=normalized.get("website") or "",
            domain=normalized.get("domain"),
            profile_url=normalized.get("profile_url") or "",
            country=normalized.get("country") or "",
            outcome=outcome,
            error_code=error_code[:64],
            error_message=_clip(error_message),
            company=company,
        )
        counter = OUTCOME_COUNTERS[outcome]
        setattr(locked, counter, getattr(locked, counter) + 1)
        locked.save(update_fields=[counter, "updated_at"])
    return row


def process_row(
    batch: ImportBatch, row_number: int, raw: Mapping[str, Any], *, user: User | None = None
) -> ImportRow:
    """The shared pipeline for one submitted company, for every channel.

    Validates ``raw`` (``validate_company_input``), then ``create_company`` into the batch's
    campaign with the batch's source, and records the outcome: ``created``, ``duplicate``,
    ``restored`` (an archived duplicate came back) or ``failed`` (invalid input, or
    ``create_company`` refused it). Validation failures never raise: they are a failed row.
    Idempotent per ``row_number``.
    """
    existing = ImportRow.objects.filter(batch=batch, row_number=row_number).first()
    if existing is not None:
        return existing
    checked = validate_company_input(raw)
    if isinstance(checked, InputErrors):
        return record_row_outcome(
            batch,
            row_number,
            ImportRowOutcome.FAILED,
            raw=raw,
            error_code=checked.first_code,
            error_message=checked.message(),
        )
    try:
        with transaction.atomic():
            result = create_company(
                batch.campaign,
                checked.name,
                checked.website,
                profile_url=checked.profile_url,
                country=checked.country,
                input_source=batch.source,
                user=user or batch.created_by,
                created_by_job_id=batch.job_id,
            )
            return record_row_outcome(
                batch,
                row_number,
                _outcome_of(result),
                raw=raw,
                company_input=checked,
                company=result.company,
            )
    except ValidationError as exc:
        return record_row_outcome(
            batch,
            row_number,
            ImportRowOutcome.FAILED,
            raw=raw,
            company_input=checked,
            error_code=COMPANY_REJECTED,
            error_message="; ".join(exc.messages),
        )
    except IntegrityError as exc:
        return record_row_outcome(
            batch,
            row_number,
            ImportRowOutcome.FAILED,
            raw=raw,
            company_input=checked,
            error_code=INTERNAL_ERROR,
            error_message=f"{type(exc).__name__}: {exc}",
        )


def _outcome_of(result: CompanyResult) -> ImportRowOutcome:
    if result.restored:
        return ImportRowOutcome.RESTORED
    if result.duplicate:
        return ImportRowOutcome.DUPLICATE
    return ImportRowOutcome.CREATED


# ------------------------------------------------------------------ finish


def finalize_batch(batch: ImportBatch, error_summary: str = "") -> None:
    """Finish a batch from its counters once every row is recorded.

    No failed rows -> ``completed``; some failed and some not -> ``partial``; every row failed
    -> ``failed``. Raises ``BatchStateError`` while rows are missing. A failed row never fails
    the batch record: it is what makes it ``partial``. A pending batch with no rows (``total_count``
    0) completes straight away.
    """
    with _locked_batch(batch) as locked:
        _require_open(locked)
        if locked.processed_count < locked.total_count:
            left = locked.total_count - locked.processed_count
            raise BatchStateError(f"Import batch {locked.pk} still has {left} unprocessed row(s).")
        _start(locked)
        if locked.failed_count == 0:
            status = ImportBatchStatus.COMPLETED
        elif locked.failed_count == locked.total_count:
            status = ImportBatchStatus.FAILED
        else:
            status = ImportBatchStatus.PARTIAL
        _move(locked, status)
        locked.error_summary = (
            _clip(error_summary) if error_summary else _summarize_failures(locked)
        )
        locked.finished_at = timezone.now()
        locked.save(update_fields=["status", "error_summary", "finished_at", "updated_at"])


def _summarize_failures(locked: ImportBatch) -> str:
    if not locked.failed_count:
        return ""
    errors = list(
        ImportRow.objects.filter(batch=locked, outcome=ImportRowOutcome.FAILED)
        .order_by("row_number")
        .values_list("row_number", "error_code")[:3]
    )
    head = f"{locked.failed_count} of {locked.total_count} row(s) failed"
    return f"{head}: " + "; ".join(f"row {n}: {code}" for n, code in errors)


def fail_batch(batch: ImportBatch, error: str) -> None:
    """The run itself broke (an unreadable file, a crashed worker): end the batch ``failed``
    whatever is left. Rows already recorded and the counters stay as they are."""
    _end(batch, ImportBatchStatus.FAILED, error)


def cancel_batch(batch: ImportBatch, reason: str = "") -> None:
    """Stop a batch on request. Rows already recorded stay (the companies exist)."""
    _end(batch, ImportBatchStatus.CANCELLED, reason)


def _end(batch: ImportBatch, status: ImportBatchStatus, message: str) -> None:
    with _locked_batch(batch) as locked:
        _require_open(locked)
        _move(locked, status)
        locked.error_summary = _clip(message)
        locked.finished_at = timezone.now()
        locked.save(update_fields=["status", "error_summary", "finished_at", "updated_at"])
