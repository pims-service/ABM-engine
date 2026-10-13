"""Serializers of the provider import endpoints (issue #67).

Input is validated here (field-level errors in the standard envelope). Output serializers
document the response shapes in OpenAPI; the views build plain dicts and run them through these.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.imports.providers.keys import PROVIDER_ID_MAX_LENGTH
from apps.imports.providers.types import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def _blank(max_length: int) -> serializers.CharField:
    return serializers.CharField(
        max_length=max_length, required=False, allow_blank=True, allow_null=True
    )


def _strings(**kwargs: Any) -> serializers.ListField:
    return serializers.ListField(
        child=serializers.CharField(max_length=100), max_length=50, required=False, **kwargs
    )


# ------------------------------------------------------------------ input


class SearchFiltersSerializer(serializers.Serializer[dict[str, Any]]):
    """Filters that replace the campaign-derived defaults. A key that is present wins: an empty
    list or `null` clears that filter; a key left out keeps the campaign value."""

    countries = _strings(help_text="ISO 3166-1 alpha-2 codes.")
    industries = _strings()
    employee_min = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    employee_max = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    business_model = serializers.CharField(
        max_length=10,
        required=False,
        allow_blank=True,
        help_text="`b2b`, `b2c`, `both` or empty for any.",
    )
    excluded_industries = _strings()
    excluded_company_types = _strings()
    sort = serializers.ChoiceField(
        choices=["relevance", "name", "employee_count_desc", "employee_count_asc"], required=False
    )


class SearchParamsSerializer(serializers.Serializer[dict[str, Any]]):
    query = serializers.CharField(max_length=200, required=False, allow_blank=True)
    filters = SearchFiltersSerializer(required=False)
    page_size = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_PAGE_SIZE, default=DEFAULT_PAGE_SIZE
    )
    cursor = serializers.CharField(max_length=512, required=False, allow_null=True)


class ProviderSearchRequestSerializer(SearchParamsSerializer):
    provider = serializers.CharField(max_length=64, help_text="Provider name from `/providers/`.")


class ProviderCompanySerializer(serializers.Serializer[dict[str, Any]]):
    """One provider company: the output of a search and the inline input of an import."""

    name = serializers.CharField(max_length=2000, allow_blank=True)
    website = _blank(2000)
    domain_hint = _blank(2000)
    profile_url = _blank(2000)
    country = _blank(50)
    industry = _blank(2000)
    employee_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    employee_range = _blank(2000)
    headquarters = _blank(2000)
    description = _blank(5000)
    provider_id = _blank(PROVIDER_ID_MAX_LENGTH)
    provider_url = _blank(2000)
    confidence = serializers.FloatField(required=False, allow_null=True)
    extra = serializers.DictField(required=False)


class ProviderImportRequestSerializer(serializers.Serializer[dict[str, Any]]):
    provider = serializers.CharField(max_length=64)
    provider_ids = serializers.ListField(
        child=serializers.CharField(max_length=PROVIDER_ID_MAX_LENGTH, allow_blank=False),
        required=False,
        help_text="Provider ids to import; they are re-found with `search` (or the campaign "
        "defaults). Give this or `companies`.",
    )
    companies = serializers.ListField(
        child=ProviderCompanySerializer(),
        required=False,
        help_text="Provider companies exactly as a preview returned them (`items[].company`).",
    )
    search = SearchParamsSerializer(
        required=False,
        help_text="The search the selection came from (same shape as the search body without "
        "`provider`): stored as provenance and used to find `provider_ids`.",
    )
    retrieved_at = serializers.DateTimeField(
        required=False, help_text="`retrieved_at` of the preview (inline companies)."
    )


# ------------------------------------------------------------------ output


class ProviderCapabilitiesSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField()
    terms = serializers.CharField(help_text="licensed_api, public_data or manual.")
    supports_search = serializers.BooleanField()
    supports_filters = serializers.DictField(child=serializers.BooleanField())
    max_page_size = serializers.IntegerField()
    requires_credentials = serializers.BooleanField()
    max_import_selection = serializers.IntegerField(help_text="Most companies per import.")
    inline_import_max = serializers.IntegerField(
        help_text="Imports up to this size finish inside the request (201); larger run in a "
        "background job (202)."
    )


class MatchedCompanySerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    website = serializers.CharField()
    country = serializers.CharField()
    archived = serializers.BooleanField()


class DedupeSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(
        choices=["new", "duplicate", "possible_duplicate", "already_imported", "invalid"]
    )
    matched_on = serializers.CharField(
        allow_null=True, help_text="domain, profile, name_country or provider_key."
    )
    company = MatchedCompanySerializer(allow_null=True)
    field_errors = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        help_text="Field errors when status is `invalid`.",
    )


class PreviewItemSerializer(serializers.Serializer[dict[str, Any]]):
    provider_key = serializers.CharField(allow_null=True)
    company = ProviderCompanySerializer()
    dedupe = DedupeSerializer()
    importable = serializers.BooleanField()


class SearchEchoSerializer(serializers.Serializer[dict[str, Any]]):
    query = serializers.CharField()
    countries = serializers.ListField(child=serializers.CharField())
    industries = serializers.ListField(child=serializers.CharField())
    employee_min = serializers.IntegerField(allow_null=True)
    employee_max = serializers.IntegerField(allow_null=True)
    business_model = serializers.CharField()
    excluded_industries = serializers.ListField(child=serializers.CharField())
    excluded_company_types = serializers.ListField(child=serializers.CharField())
    sort = serializers.CharField()
    page_size = serializers.IntegerField()
    limit = serializers.IntegerField()
    cursor = serializers.CharField(allow_null=True)


class ProviderSearchResponseSerializer(serializers.Serializer[dict[str, Any]]):
    provider = serializers.CharField()
    retrieved_at = serializers.DateTimeField()
    search = SearchEchoSerializer(help_text="The effective search (campaign defaults + overrides).")
    items = PreviewItemSerializer(many=True)
    counts = serializers.DictField(child=serializers.IntegerField())
    next_cursor = serializers.CharField(allow_null=True)
    total_estimate = serializers.IntegerField(allow_null=True)
    warnings = serializers.ListField(child=serializers.CharField())


class ImportRowSerializer(serializers.Serializer[dict[str, Any]]):
    row_number = serializers.IntegerField()
    name = serializers.CharField()
    outcome = serializers.CharField(help_text="created, duplicate, restored, skipped or failed.")
    error_code = serializers.CharField()
    error_message = serializers.CharField()
    company_id = serializers.UUIDField(allow_null=True)
    match_strength = serializers.CharField(help_text="strong, weak or blank.")
    matched_on = serializers.CharField()
    possible_duplicate = serializers.BooleanField(
        help_text="Created, but flagged as a possible duplicate of `candidate_id`."
    )
    candidate_id = serializers.UUIDField(allow_null=True)


class ProviderImportBatchSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    campaign = serializers.UUIDField()
    provider = serializers.CharField()
    status = serializers.CharField(help_text="pending, processing, completed, partial, failed.")
    background = serializers.BooleanField(help_text="Processed by a background job.")
    job_id = serializers.UUIDField(allow_null=True)
    total_count = serializers.IntegerField()
    created_count = serializers.IntegerField()
    duplicate_count = serializers.IntegerField()
    restored_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    processed_count = serializers.IntegerField()
    progress_percent = serializers.IntegerField()
    error_summary = serializers.CharField()
    provider_error = serializers.DictField(allow_null=True, required=False)
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    rows = ImportRowSerializer(many=True)
