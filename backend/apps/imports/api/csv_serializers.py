"""Serializers of the CSV import endpoints (issue #62): the multipart request, the batch
representation and the shapes of the preview and the 202 response (documentation + validation of
what the views build; the views assemble the dicts from the parser results)."""

from __future__ import annotations

import json
from typing import Any

from rest_framework import serializers

from apps.imports.models import ImportBatch
from apps.imports.services import MAX_MAPPING_BYTES


class ImportBatchSerializer(serializers.ModelSerializer[ImportBatch]):
    """One import batch with its counters. The counters are the source of truth for the outcome;
    ``job`` is the background run (``/jobs`` progress is derived from the same counters)."""

    source = serializers.CharField(  # type: ignore[assignment]
        read_only=True, help_text="How the batch was fed: `manual`, `csv` or `provider`."
    )
    processed_count = serializers.IntegerField(read_only=True)
    progress_percent = serializers.IntegerField(
        read_only=True, help_text="0 to 100: processed rows over total rows."
    )
    column_mapping = serializers.DictField(
        child=serializers.CharField(), read_only=True, help_text="`{file column: company field}`."
    )

    class Meta:
        model = ImportBatch
        fields = (
            "id",
            "campaign",
            "client",
            "source",
            "status",
            "original_filename",
            "file_size",
            "file_sha256",
            "column_mapping",
            "total_count",
            "created_count",
            "duplicate_count",
            "restored_count",
            "skipped_count",
            "failed_count",
            "processed_count",
            "progress_percent",
            "error_summary",
            "job",
            "created_by",
            "created_at",
            "started_at",
            "finished_at",
        )
        read_only_fields = fields


class CsvUploadSerializer(serializers.Serializer[dict[str, Any]]):
    """``multipart/form-data`` body of the preview and the import."""

    file = serializers.FileField(
        allow_empty_file=True,  # an empty file is answered by the parser (`empty_file`)
        help_text="The CSV file. Any content type is accepted; the bytes decide (see the "
        "limits in the preview response).",
    )
    mapping = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text='Optional JSON object `{"<file column>": "<field>"}` with fields `name`, '
        "`website`, `profile_url`, `country`; `name` is required. Left out, the mapping "
        "suggested from the headers is used.",
    )

    def validate_mapping(self, value: str) -> dict[str, str] | None:
        if not value.strip():
            return None
        if len(value.encode("utf-8")) > MAX_MAPPING_BYTES:
            raise serializers.ValidationError("The mapping is too large.")
        try:
            data = json.loads(value)
        except ValueError:
            raise serializers.ValidationError("Must be a JSON object.") from None
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            raise serializers.ValidationError("Must be a JSON object of column name to field name.")
        return data


class CsvIssueSerializer(serializers.Serializer[dict[str, Any]]):
    field = serializers.CharField(
        help_text="Company field the problem is about; blank for the row."
    )
    code = serializers.CharField(help_text="Stable code (`apps.imports.csvparse` / `schema`).")
    message = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)


class CsvPreviewRowSerializer(serializers.Serializer[dict[str, Any]]):
    row_number = serializers.IntegerField(help_text="Record position after the header (1-based).")
    raw = serializers.DictField(child=serializers.CharField(), help_text="Every column.")
    mapped = serializers.DictField(
        child=serializers.CharField(), help_text="The company fields (empty for a broken row)."
    )
    errors = CsvIssueSerializer(  # type: ignore[assignment]
        many=True, help_text="Why the import would fail this row."
    )


class CsvPreviewFileSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField()
    size_bytes = serializers.IntegerField()
    sha256 = serializers.CharField()
    encoding = serializers.CharField()
    delimiter = serializers.CharField()


class CsvLimitsSerializer(serializers.Serializer[dict[str, Any]]):
    max_file_bytes = serializers.IntegerField()
    max_rows = serializers.IntegerField()
    max_columns = serializers.IntegerField()
    max_cell_length = serializers.IntegerField()


class CsvPreviewSerializer(serializers.Serializer[dict[str, Any]]):
    """200 response of the preview. Nothing was stored."""

    file = CsvPreviewFileSerializer()
    headers = serializers.ListField(child=serializers.CharField())
    suggested_mapping = serializers.DictField(
        child=serializers.CharField(), help_text="Guessed from the headers."
    )
    mapping = serializers.DictField(
        child=serializers.CharField(),
        help_text="The mapping the rows below were read with (yours, else the suggestion).",
    )
    unmapped_headers = serializers.ListField(child=serializers.CharField())
    fuzzy_headers = serializers.ListField(
        child=serializers.CharField(), help_text="Matched only approximately: worth a look."
    )
    company_fields = serializers.ListField(
        child=serializers.CharField(), help_text="The company fields a column may be mapped to."
    )
    rows = CsvPreviewRowSerializer(many=True)
    total_rows = serializers.IntegerField(help_text="Non-blank data rows in the whole file.")
    blank_rows = serializers.IntegerField()
    error_rows = serializers.IntegerField(
        help_text="Rows of the whole file the parser can already tell are broken."
    )
    warnings = CsvIssueSerializer(many=True)
    limits = CsvLimitsSerializer()
    active_import = serializers.UUIDField(
        allow_null=True,
        help_text="Id of a pending/processing batch of this same file in this campaign (the "
        "import would be refused with 409), else null.",
    )


class CsvImportAcceptedSerializer(serializers.Serializer[dict[str, Any]]):
    """202 response: the work is queued (or, in tests, already done)."""

    batch = ImportBatchSerializer()
    job_id = serializers.UUIDField()
    status_url = serializers.CharField(help_text="Where to poll the batch: `GET {status_url}`.")
