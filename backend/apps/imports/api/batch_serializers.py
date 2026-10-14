"""Serializers of the import batch read API (issue #63): batch detail with its result summary,
the history list query, and the row results (list and filters)."""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from apps.companies.models import Company
from apps.imports.models import (
    ROW_MATCH_STRENGTHS,
    ImportBatch,
    ImportBatchStatus,
    ImportRow,
    ImportRowOutcome,
    ImportSource,
)

from .csv_serializers import ImportBatchSerializer

#: Most error codes listed in a batch's ``summary.error_codes``.
TOP_ERROR_CODES = 10

#: What the rows endpoint shows of the stored ``raw_data`` (which is already scrubbed of
#: credentials and capped at 16 KiB when it is saved): the first keys and the start of each value.
RAW_MAX_KEYS = 30
RAW_MAX_VALUE_CHARS = 300
RAW_TRUNCATED_KEY = "_truncated"


def capped_raw_data(raw: Any) -> dict[str, Any]:
    """The stored (already sanitised) ``raw_data``, shortened for display: at most
    ``RAW_MAX_KEYS`` keys and ``RAW_MAX_VALUE_CHARS`` characters per text value. ``_truncated`` is
    true when anything was cut here (or was already cut when it was stored)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    cut = False
    for key, value in raw.items():
        if len(out) >= RAW_MAX_KEYS:
            cut = True
            break
        if key == RAW_TRUNCATED_KEY:
            cut = cut or bool(value)
            continue
        if isinstance(value, str) and len(value) > RAW_MAX_VALUE_CHARS:
            value = value[:RAW_MAX_VALUE_CHARS]
            cut = True
        elif isinstance(value, dict | list):
            value = str(value)[:RAW_MAX_VALUE_CHARS]
        out[str(key)] = value
    if cut:
        out[RAW_TRUNCATED_KEY] = True
    return out


# ------------------------------------------------------------------ batch detail


class ErrorCodeCountSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.CharField(help_text="Stable error code of the row (`error_code`).")
    count = serializers.IntegerField()


class OutcomeCountsSerializer(serializers.Serializer[dict[str, Any]]):
    created = serializers.IntegerField()
    duplicate = serializers.IntegerField()
    restored = serializers.IntegerField()
    skipped = serializers.IntegerField()
    failed = serializers.IntegerField()


class ImportBatchSummarySerializer(serializers.Serializer[dict[str, Any]]):
    """The result of a batch without paging its rows."""

    outcomes = OutcomeCountsSerializer(
        help_text="Rows per outcome (the batch counters: they always equal the row outcomes)."
    )
    error_codes = ErrorCodeCountSerializer(
        many=True,
        help_text="Failed and skipped rows grouped by `error_code`, most frequent first (at "
        f"most {TOP_ERROR_CODES}).",
    )
    weak_duplicates = serializers.IntegerField(
        help_text="Rows that were created but flagged as a possible duplicate (weak match)."
    )


class ImportBatchDetailSerializer(ImportBatchSerializer):
    """``GET /imports/{batch_id}/``: the batch with its result summary. The view attaches
    ``summary`` (a dict) to the instance before serializing."""

    summary = ImportBatchSummarySerializer(read_only=True)
    job_status = serializers.CharField(
        read_only=True,
        allow_null=True,
        help_text="Status of the background job (`queued`, `running`, `succeeded`, ...), or "
        "null when the batch has no job (manual entry).",
    )
    campaign_name = serializers.CharField(read_only=True, source="campaign.name")

    class Meta(ImportBatchSerializer.Meta):
        fields = (*ImportBatchSerializer.Meta.fields, "campaign_name", "job_status", "summary")  # type: ignore[assignment]
        read_only_fields = fields  # type: ignore[assignment]


# ------------------------------------------------------------------ history list


class ImportBatchListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    """Query parameters of the campaign's import history (validated by the view; documents them
    for OpenAPI). Newest first unless `ordering` says otherwise."""

    source = serializers.ChoiceField(  # type: ignore[assignment]
        choices=ImportSource.choices, required=False, help_text="`csv`, `manual` or `provider`."
    )
    status = serializers.ChoiceField(
        choices=ImportBatchStatus.choices, required=False, help_text="Only batches in this status."
    )
    created_by = serializers.UUIDField(required=False, help_text="Id of the user who started it.")
    created_from = serializers.DateField(
        required=False, help_text="Started on or after this date (`YYYY-MM-DD`, server time zone)."
    )
    created_to = serializers.DateField(
        required=False, help_text="Started on or before this date (`YYYY-MM-DD`)."
    )
    ordering = serializers.ChoiceField(
        choices=[
            f"{sign}{name}"
            for name in ("created_at", "finished_at", "total_count", "failed_count")
            for sign in ("", "-")
        ],
        required=False,
        help_text="Default `-created_at` (newest first).",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start, end = attrs.get("created_from"), attrs.get("created_to")
        if start and end and start > end:
            raise serializers.ValidationError(
                {"created_to": "Must not be before `created_from`."}, code="invalid_range"
            )
        return attrs


# ------------------------------------------------------------------ rows


class CompanyRefSerializer(serializers.ModelSerializer[Company]):
    """Just enough of a company to show and link it."""

    class Meta:
        model = Company
        fields = ("id", "name", "domain")
        read_only_fields = fields


@extend_schema_serializer(component_name="ImportBatchRow")
class ImportRowSerializer(serializers.ModelSerializer[ImportRow]):
    """One row result: what was submitted, what validation made of it and what happened.

    The OpenAPI component is named ``ImportBatchRow`` because the provider-import API has its
    own, different ``ImportRow`` component.
    """

    company = CompanyRefSerializer(
        read_only=True,
        allow_null=True,
        help_text="The created company, or the existing one for a duplicate or restored row.",
    )
    candidate = CompanyRefSerializer(
        read_only=True,
        allow_null=True,
        help_text="The matched company: the existing one (strong) or the possible duplicate "
        "(weak). Null when nothing matched.",
    )
    raw_data = serializers.SerializerMethodField(
        help_text="What was submitted, as stored: scrubbed of credentials, at most 30 keys and "
        "300 characters per value (`_truncated: true` when anything was cut)."
    )

    class Meta:
        model = ImportRow
        fields = (
            "id",
            "row_number",
            "outcome",
            "match_strength",
            "matched_on",
            "name",
            "website",
            "domain",
            "profile_url",
            "country",
            "error_code",
            "error_message",
            "company",
            "candidate",
            "raw_data",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.DictField())
    def get_raw_data(self, obj: ImportRow) -> dict[str, Any]:
        return capped_raw_data(obj.raw_data)


class ImportRowListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    """Query parameters of the row results. `search` (name, domain) is the usual DRF parameter."""

    outcome = serializers.ChoiceField(
        choices=ImportRowOutcome.choices, required=False, help_text="Only rows with this outcome."
    )
    has_error = serializers.ChoiceField(
        choices=["true", "false"],
        required=False,
        help_text="`true`: only rows with an `error_code` (failed and skipped); `false`: the rest.",
    )
    error_code = serializers.CharField(
        required=False, max_length=64, help_text="Only rows with exactly this error code."
    )
    match_strength = serializers.ChoiceField(
        choices=ROW_MATCH_STRENGTHS,
        required=False,
        help_text="`strong` (linked to an existing company) or `weak` (possible duplicate).",
    )
    search = serializers.CharField(
        required=False, max_length=200, help_text="Case-insensitive text in the name or domain."
    )
    ordering = serializers.ChoiceField(
        choices=["row_number", "-row_number"],
        required=False,
        help_text="Default `row_number` (file order).",
    )


class ErrorReportQuerySerializer(serializers.Serializer[dict[str, Any]]):
    include = serializers.ChoiceField(
        choices=["duplicates"],
        required=False,
        help_text="`duplicates`: also list the rows that matched an existing company "
        "(`duplicate` and `restored`). Default: failed and skipped rows only.",
    )


def batch_summary(
    batch: ImportBatch, error_codes: list[dict[str, Any]], weak: int
) -> dict[str, Any]:
    return {
        "outcomes": {
            "created": batch.created_count,
            "duplicate": batch.duplicate_count,
            "restored": batch.restored_count,
            "skipped": batch.skipped_count,
            "failed": batch.failed_count,
        },
        "error_codes": error_codes,
        "weak_duplicates": weak,
    }
