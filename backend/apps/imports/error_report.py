"""The error report of an import batch (issue #63): a CSV of the rows that need attention, in the
shape the CSV import reads back, so a user can fix it and upload it again.

* Rows: ``failed`` and ``skipped`` ones; ``include_duplicates`` adds the rows that matched an
  existing company (``duplicate``, ``restored``). Created rows are never listed (a weak match that
  was still created is a flag, not an error).
* Columns: ``row_number, name, website, profile_url, country, outcome, error_code,
  error_message``. The four company columns hold what the user entered (``raw_data``, found
  through the batch's column mapping for rows that were never normalized). The extra columns are
  not company fields, so the importer ignores them on re-upload.
* UTF-8 with a BOM (Excel shows Arabic correctly). Every cell goes through ``safe_cell`` so a
  value starting with ``=``, ``+``, ``-`` or ``@`` cannot run as a formula.
* ``iter_error_report`` yields bytes chunk by chunk from a database cursor: memory does not grow
  with the number of rows.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from typing import Any

from apps.imports.csvparse import FIELDS, safe_row
from apps.imports.models import ImportBatch, ImportRow, ImportRowOutcome

REPORT_COLUMNS = (
    "row_number",
    *FIELDS,
    "outcome",
    "error_code",
    "error_message",
)
REPORT_CONTENT_TYPE = "text/csv; charset=utf-8"
BOM = "﻿"
CHUNK_ROWS = 500

ERROR_OUTCOMES = (ImportRowOutcome.FAILED, ImportRowOutcome.SKIPPED)
DUPLICATE_OUTCOMES = (ImportRowOutcome.DUPLICATE, ImportRowOutcome.RESTORED)


def report_filename(batch: ImportBatch) -> str:
    """``import-<batch id>-errors.csv``: derived from the id only, never from user input."""
    return f"import-{batch.pk}-errors.csv"


def report_rows(batch: ImportBatch, *, include_duplicates: bool = False) -> Any:
    """The batch's rows that belong in the report, in file order."""
    outcomes = ERROR_OUTCOMES + (DUPLICATE_OUTCOMES if include_duplicates else ())
    return ImportRow.objects.filter(batch=batch, outcome__in=outcomes).order_by("row_number")


def _text(value: Any) -> str:
    return (
        str(value) if isinstance(value, str | int | float) and not isinstance(value, bool) else ""
    )


def entered_values(row: ImportRow, headers_by_field: Mapping[str, str]) -> list[str]:
    """What the user entered for each company field. Rows that were processed keep the mapped
    value under the field name; rows rejected before that keep the file's own column names, which
    ``headers_by_field`` (the inverse of the batch's column mapping) leads back to the field."""
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    normalized = {"name": row.name, "website": row.website, "profile_url": row.profile_url}
    normalized["country"] = row.country
    values = []
    for field in FIELDS:
        value = _text(raw.get(field))
        if not value and field in headers_by_field:
            value = _text(raw.get(headers_by_field[field]))
        values.append(value or normalized[field])
    return values


class _Echo:
    """A file-like object whose ``write`` hands the text back: csv.writer then builds one line at
    a time and nothing is buffered."""

    def write(self, value: str) -> str:
        return value


def iter_error_report(batch: ImportBatch, *, include_duplicates: bool = False) -> Iterator[bytes]:
    writer = csv.writer(_Echo(), lineterminator="\r\n")
    headers_by_field: dict[str, str] = {}
    mapping = batch.column_mapping if isinstance(batch.column_mapping, dict) else {}
    for header, field in mapping.items():
        headers_by_field.setdefault(str(field), str(header))
    yield (BOM + writer.writerow(REPORT_COLUMNS)).encode("utf-8")
    pending: list[str] = []
    rows = report_rows(batch, include_duplicates=include_duplicates).only(
        "row_number",
        "raw_data",
        "name",
        "website",
        "profile_url",
        "country",
        "outcome",
        "error_code",
        "error_message",
    )
    for row in rows.iterator(chunk_size=CHUNK_ROWS):
        cells = [
            row.row_number,
            *entered_values(row, headers_by_field),
            row.outcome,
            row.error_code,
            row.error_message,
        ]
        pending.append(writer.writerow(safe_row(cells)))
        if len(pending) >= CHUNK_ROWS:
            yield "".join(pending).encode("utf-8")
            pending = []
    if pending:
        yield "".join(pending).encode("utf-8")
