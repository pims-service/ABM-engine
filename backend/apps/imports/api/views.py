"""Provider import API (issue #67): list providers, preview a search, import a selection.

    GET  /api/v1/providers/                              READ    registered providers
    POST /api/v1/campaigns/{id}/provider-search/         EDIT    preview (writes nothing)
    POST /api/v1/campaigns/{id}/provider-import/         EDIT    import selected (201 or 202)
    GET  /api/v1/provider-imports/{batch_id}/            READ    batch status and row outcomes

Client scoped like every endpoint (docs/permissions.md): a campaign or batch of a client the user
is not a member of is a 404 (checked before the body is read), a role below the level is a 403.
Provider failures are safe envelope errors (``errors.py``); upstream text never reaches a
response or a log. Per-user throttling: scopes ``provider_search`` and ``provider_import`` in
``DEFAULT_THROTTLE_RATES`` (``429 throttled`` with ``retry_after``; #69 makes them configurable).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, ClassVar, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.core.permissions import ClientScopedViewSet
from apps.core.roles import Level
from apps.core.schema import error_responses
from apps.imports import provider_import as service
from apps.imports.models import ImportBatch, ImportRow, ImportSource
from apps.imports.providers import (
    CompanySearchRequest,
    ProviderBadRequest,
    ProviderError,
    ProviderRegistryError,
    default_registry,
)

from .errors import ProviderAPIError
from .serializers import (
    ProviderCapabilitiesSerializer,
    ProviderImportBatchSerializer,
    ProviderImportRequestSerializer,
    ProviderSearchRequestSerializer,
    ProviderSearchResponseSerializer,
)

TAGS = ["provider import"]


def _levels(level: str, who: str) -> str:
    return (
        f"Needs the `{level}` level: {who}. Others get 403; campaigns of clients you are not a "
        "member of give 404."
    )


class ScopedThrottleMixin:
    """The normal throttles plus the view's ``throttle_scope`` (per authenticated user)."""

    throttle_scope: str | None = None

    def get_throttles(self) -> list[BaseThrottle]:
        throttles: list[BaseThrottle] = super().get_throttles()  # type: ignore[misc]
        if self.throttle_scope:
            throttles.append(ScopedRateThrottle())
        return throttles


@contextmanager
def api_errors() -> Iterator[None]:
    """Service and provider errors as API errors (see ``errors.py`` for the provider ones)."""
    try:
        yield
    except ProviderRegistryError as exc:
        raise ValidationError({"provider": [exc.message]}) from None
    except ProviderError as exc:
        raise ProviderAPIError(exc) from None
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise ValidationError(exc.message_dict) from exc
        raise ValidationError({"non_field_errors": exc.messages}) from exc


def build_search_request(campaign: Campaign, params: Mapping[str, Any]) -> CompanySearchRequest:
    """Campaign-derived default, then the user's filter overrides. Bad values are a 400 on the
    field that is wrong."""
    try:
        request = service.default_search_request(
            campaign,
            query=params.get("query", ""),
            page_size=params.get("page_size", 25),
            cursor=params.get("cursor"),
        )
        return service.apply_filter_overrides(request, params.get("filters", {}))
    except ProviderBadRequest as exc:
        field = exc.field or "filters"
        raise ValidationError({field: ["This value is not valid for the search."]}) from None


def _company_brief(company: Company | None) -> dict[str, Any] | None:
    if company is None:
        return None
    return {
        "id": company.pk,
        "name": company.name,
        "website": company.website,
        "country": company.country,
        "archived": company.is_archived,
    }


def _item(item: service.PreviewItem) -> dict[str, Any]:
    return {
        "provider_key": item.provider_key,
        "company": item.company,
        "dedupe": {
            "status": item.status.value,
            "matched_on": item.matched_on,
            "company": _company_brief(item.matched_company),
            "field_errors": [{"field": f, "code": c, "message": m} for f, c, m in item.errors],
        },
        "importable": item.importable,
    }


def batch_payload(
    batch: ImportBatch, *, background: bool, provider_error: dict[str, object] | None = None
) -> dict[str, Any]:
    rows = ImportRow.objects.filter(batch=batch).select_related("company").order_by("row_number")
    return {
        "id": batch.pk,
        "campaign": batch.campaign_id,
        "provider": batch.column_mapping.get("provider", ""),
        "status": batch.status,
        "background": background,
        "job_id": batch.job_id,
        "total_count": batch.total_count,
        "created_count": batch.created_count,
        "duplicate_count": batch.duplicate_count,
        "restored_count": batch.restored_count,
        "skipped_count": batch.skipped_count,
        "failed_count": batch.failed_count,
        "processed_count": batch.processed_count,
        "progress_percent": batch.progress_percent,
        "error_summary": batch.error_summary,
        "provider_error": provider_error,
        "started_at": batch.started_at,
        "finished_at": batch.finished_at,
        "rows": [
            {
                "row_number": r.row_number,
                "name": r.name or str(r.raw_data.get("name", "")),
                "outcome": r.outcome,
                "error_code": r.error_code,
                "error_message": r.error_message,
                "company_id": r.company_id,
                "match_strength": r.match_strength,
                "matched_on": r.matched_on,
                "possible_duplicate": bool(r.outcome == "created" and r.match_strength == "weak"),
                "candidate_id": r.candidate_id,
            }
            for r in rows
        ],
    }


class ProviderViewSet(ClientScopedViewSet):
    """The registered providers (not tenant data: any signed-in member may list them)."""

    queryset = Campaign.objects.none()
    action_levels: ClassVar[dict[str, Level]] = {"list": Level.READ}

    @extend_schema(
        tags=TAGS,
        operation_id="providers_list",
        summary="List company data providers",
        description="The providers registered in this deployment, with their capabilities "
        "(which filters they apply, page size, terms). No credentials or settings are ever "
        "included. The `fake` provider exists in dev and test only. An empty list means no "
        "provider is configured. " + _levels("READ", "any member"),
        responses={
            200: ProviderCapabilitiesSerializer(many=True),
            **error_responses(401, 403, 429, 500),
        },
    )
    def list(self, request: Request) -> Response:
        rows = [
            {
                **service.describe_provider(p),
                "max_import_selection": service.max_selection(),
                "inline_import_max": service.inline_threshold(),
            }
            for p in default_registry.list()
        ]
        page = self.paginate_queryset(rows)
        return self.get_paginated_response(
            ProviderCapabilitiesSerializer(page or [], many=True).data  # type: ignore[arg-type]
        )


class CampaignProviderViewSet(ScopedThrottleMixin, ClientScopedViewSet):
    """Preview and import for one campaign (the campaign is the URL id)."""

    queryset = Campaign.objects.all()
    action_levels: ClassVar[dict[str, Level]] = {
        "search": Level.EDIT,
        "import_selected": Level.EDIT,
    }

    def _user(self) -> User:
        return cast(User, self.request.user)

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_provider_search",
        summary="Preview a provider search",
        description="Searches the provider with the campaign's rules as default filters "
        "(`filters` override them, `query` adds free text) and returns one page of companies "
        "annotated with what an import would do: `dedupe.status` is `new`, `duplicate` (strong "
        "match with a company already in the campaign), `possible_duplicate` (weak match), "
        "`already_imported` (this provider id was imported into the campaign before) or "
        "`invalid`. **Nothing is written.** Page with `cursor` = the previous `next_cursor`. "
        "Provider failures: 429 `provider_rate_limited` (`details.retry_after`), 503 "
        "`provider_unavailable`, 502 `provider_auth` / `provider_quota_exceeded`, 400 "
        "`provider_bad_request`; the messages are fixed and never contain upstream text. "
        "Throttled per user (scope `provider_search`). " + _levels("EDIT", "manager and admin"),
        request=ProviderSearchRequestSerializer,
        responses={
            200: ProviderSearchResponseSerializer,
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    def search(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()  # 404 / 403 before the body is looked at
        serializer = ProviderSearchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        search = build_search_request(campaign, data)
        with api_errors():
            preview = service.preview_provider_search(
                campaign, data["provider"], search, user=self._user()
            )
        body = {
            "provider": preview.provider,
            "retrieved_at": preview.result.retrieved_at,
            "search": service.request_snapshot(preview.request),
            "items": [_item(i) for i in preview.items],
            "counts": preview.counts,
            "next_cursor": preview.result.next_cursor,
            "total_estimate": preview.result.total_estimate,
            "warnings": list(preview.result.warnings),
        }
        return Response(ProviderSearchResponseSerializer(body).data)

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_provider_import",
        summary="Import selected provider companies",
        description="Imports the selection into the campaign as one `provider` import batch. "
        "Give `companies` (the `items[].company` objects of a preview) or `provider_ids` "
        "(found again with `search`, default the campaign's rules). At most "
        "`max_import_selection` (see `/providers/`, default 100) per request, else 400. Every "
        "company goes through the same validation and duplicate handling as manual and CSV "
        "rows: strong duplicates link to the existing company (never edited), weak ones are "
        "created and flagged, and a provider id already imported into the campaign is always a "
        "`duplicate` row, never a second company. Each created company records the provider, "
        "provider id and `retrieved_at` as a data source. Up to `inline_import_max` (25) companies "
        "are processed in the request (**201**, batch finished); larger selections run in a "
        "background job (**202**, poll `GET /provider-imports/{id}/`). A provider failure while "
        "finding ids leaves the batch `partial` or `failed` with the safe provider error code on "
        "the affected rows. Throttled per user (scope `provider_import`). "
        + _levels("EDIT", "manager and admin"),
        request=ProviderImportRequestSerializer,
        responses={
            201: ProviderImportBatchSerializer,
            202: ProviderImportBatchSerializer,
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    def import_selected(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        serializer = ProviderImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        retrieved_at = data.get("retrieved_at")
        if retrieved_at is not None and retrieved_at > timezone.now() + timedelta(minutes=1):
            raise ValidationError({"retrieved_at": ["Must not be in the future."]})
        search = build_search_request(campaign, data["search"]) if "search" in data else None
        with api_errors():
            result = service.import_provider_companies(
                campaign,
                data["provider"],
                provider_ids=data.get("provider_ids"),
                companies=data.get("companies"),
                search=search,
                retrieved_at=retrieved_at,
                user=self._user(),
            )
        body = batch_payload(
            result.batch, background=result.background, provider_error=result.provider_error
        )
        return Response(
            ProviderImportBatchSerializer(body).data, status=202 if result.background else 201
        )


class ProviderImportViewSet(ClientScopedViewSet):
    """Status of a provider import batch (poll this after a 202)."""

    queryset = ImportBatch.objects.filter(source=ImportSource.PROVIDER)
    action_levels: ClassVar[dict[str, Level]] = {"retrieve": Level.READ}

    @extend_schema(
        tags=TAGS,
        operation_id="provider_imports_retrieve",
        summary="Get a provider import batch",
        description="Status, counters and the outcome of every row of a provider import batch "
        "(`background` is true while a job processes it: poll until `status` is `completed`, "
        "`partial` or `failed`). " + _levels("READ", "any member"),
        responses={200: ProviderImportBatchSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        batch = self.get_object()
        body = batch_payload(batch, background=batch.job_id is not None)
        return Response(ProviderImportBatchSerializer(body).data)
