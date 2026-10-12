"""Serializers for the company endpoints (issue #59).

Input is parsed here (types, unknown fields) and then validated by
``apps.imports.manual.validate_manual_entry`` so the error codes are the stable ones of
``apps.imports.schema``. Companies are never edited through the API: there is no update
serializer.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.campaigns.api.campaign_serializers import RejectUnknownFields, StrictCharField
from apps.companies.models import Company, CompanyResearch, CompanyStatus, InputSource
from apps.core.models import Job
from apps.imports.models import ImportRowOutcome


class PossibleDuplicateOfSerializer(serializers.ModelSerializer[Company]):
    """A short pointer to another company of the same campaign."""

    class Meta:
        model = Company
        fields = ("id", "name", "website", "domain", "archived_at")
        read_only_fields = fields


class AnalysisJobSerializer(serializers.ModelSerializer[Job]):
    """The background job behind an "Analyze Company" request (see `GET` job status in M4)."""

    class Meta:
        model = Job
        fields = (
            "id",
            "type",
            "status",
            "total_count",
            "done_count",
            "failed_count",
            "error_summary",
            "created_at",
            "started_at",
            "finished_at",
        )
        read_only_fields = fields


class ResearchSummarySerializer(serializers.ModelSerializer[CompanyResearch]):
    """The current (latest) research snapshot of a company. Null facts mean "not found"."""

    class Meta:
        model = CompanyResearch
        fields = (
            "id",
            "researched_at",
            "industry",
            "description",
            "headquarters",
            "employee_count",
            "business_model",
            "classification",
            "data_source",
        )
        read_only_fields = fields


class CompanySerializer(serializers.ModelSerializer[Company]):
    """A company as listed. ``analysis`` is its newest analysis job (null if never requested)."""

    possible_duplicate = serializers.BooleanField(
        read_only=True,
        help_text="True when it was created next to a weak (name and country) match.",
    )
    possible_duplicate_of = PossibleDuplicateOfSerializer(read_only=True, allow_null=True)
    analysis = AnalysisJobSerializer(source="analysis_job", read_only=True, allow_null=True)

    class Meta:
        model = Company
        fields: tuple[str, ...] = (
            "id",
            "campaign",
            "client",
            "name",
            "website",
            "domain",
            "profile_url",
            "country",
            "input_source",
            "status",
            "possible_duplicate",
            "possible_duplicate_of",
            "analysis",
            "archived_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class CompanyDetailSerializer(CompanySerializer):
    """A company with its latest research snapshot (null until research exists)."""

    latest_research = ResearchSummarySerializer(read_only=True, allow_null=True)

    class Meta(CompanySerializer.Meta):
        fields = (*CompanySerializer.Meta.fields, "latest_research")
        read_only_fields = fields


class CompanyListQuerySerializer(serializers.Serializer[dict[str, str]]):
    """Query parameters of the company list (validated by the view; documents them for OpenAPI).
    `search` (name, domain, website) and `ordering` are the usual DRF parameters."""

    status = serializers.ChoiceField(
        choices=CompanyStatus.choices, required=False, help_text="Only companies with this status."
    )
    archived = serializers.ChoiceField(
        choices=["false", "true", "all"],
        required=False,
        help_text="`false` (default) hides archived companies, `true` shows only archived "
        "ones, `all` shows both.",
    )
    possible_duplicate = serializers.ChoiceField(
        choices=["true", "false"],
        required=False,
        help_text="`true`: only companies flagged as a possible duplicate; `false`: the rest.",
    )
    input_source = serializers.ChoiceField(
        choices=InputSource.choices, required=False, help_text="How the company came in."
    )


class ManualCompanySerializer(RejectUnknownFields, serializers.Serializer[dict[str, Any]]):
    """Body of `POST /campaigns/{id}/companies/`.

    Send `name` plus `website` and/or `profile_url`, or use `input` (one free-text box) for one
    of them. Values are validated with the stable codes of the import schema; errors look like
    `{"details": {"website": [{"code": "website_invalid", "message": "..."}]}}`.
    """

    name = StrictCharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=False,
        help_text="Company name (required unless `input` is a name). At most 300 characters.",
    )  # fmt: skip
    website = StrictCharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=False,
        help_text="Website or domain, e.g. `acme.com` or `https://www.acme.com/about`.",
    )  # fmt: skip
    profile_url = StrictCharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=False,
        help_text="Public company profile URL, e.g. `https://www.linkedin.com/company/acme`. "
        "Used as an identifier only; it is never fetched.",
    )  # fmt: skip
    country = StrictCharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=False,
        help_text="ISO 3166-1 alpha-2 code, any case.",
    )  # fmt: skip
    input = StrictCharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=False,
        help_text="One free-text box. A URL or domain fills `website`, a company profile URL "
        "fills `profile_url`, anything else fills `name`; it only fills a field you left empty "
        "(`input_conflicts_with_field` otherwise).",
    )  # fmt: skip
    analyze = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Also queue an analysis (same as calling `analyze` next).",
    )


class ImportRowSummarySerializer(serializers.Serializer[dict[str, Any]]):
    """What the one-row manual import batch recorded for this entry."""

    id = serializers.UUIDField()
    row_number = serializers.IntegerField(help_text="Always 1 for a manual entry.")
    outcome = serializers.ChoiceField(choices=ImportRowOutcome.choices)
    match_strength = serializers.CharField(
        allow_blank=True, help_text="`strong`, `weak` or blank (no match)."
    )
    matched_on = serializers.CharField(
        allow_blank=True, help_text="`domain`, `profile`, `name` or blank."
    )


class ManualEntryResultSerializer(serializers.Serializer[dict[str, Any]]):
    """Result of a manual entry: 201 when `created`, 200 for an existing company."""

    outcome = serializers.ChoiceField(
        choices=[
            ImportRowOutcome.CREATED,
            ImportRowOutcome.DUPLICATE,
            ImportRowOutcome.RESTORED,
        ],
        help_text="`created`, `duplicate` (strong match: the existing company is returned "
        "unchanged) or `restored` (a strong match that was archived is back).",
    )
    created = serializers.BooleanField()
    duplicate = serializers.BooleanField(help_text="True for a strong match (outcome not created).")
    restored = serializers.BooleanField(help_text="True when an archived duplicate was restored.")
    possible_duplicate = serializers.BooleanField(
        help_text="True when a NEW company was created next to a weak match."
    )
    possible_duplicate_of = PossibleDuplicateOfSerializer(allow_null=True)
    company = CompanyDetailSerializer()
    batch_id = serializers.UUIDField(help_text="The one-row manual ImportBatch.")
    import_row = ImportRowSummarySerializer()
    analysis = AnalysisJobSerializer(
        allow_null=True,
        help_text="The analysis job when `analyze` was true (or one was already queued).",
    )


class AnalysisRequestSerializer(serializers.Serializer[dict[str, Any]]):
    """Result of `POST /companies/{id}/analyze/` (HTTP 202)."""

    company_id = serializers.UUIDField()
    job = AnalysisJobSerializer()
    already_queued = serializers.BooleanField(
        help_text="True when the company already had a queued or running job: that job is "
        "returned and nothing new is queued."
    )
