"""Import batch endpoints (issues #62 and #63), built on ``ClientScopedViewSet``
(docs/permissions.md):

* ``GET  /api/v1/imports/{batch_id}/``: status, counters, progress and result summary (READ).
* ``GET  /api/v1/imports/{batch_id}/rows/``: paginated, filterable row results (READ).
* ``GET  /api/v1/imports/{batch_id}/errors.csv``: the error report download (READ).
* ``POST /api/v1/imports/{batch_id}/cancel/``: stop a batch (EDIT, issue #62).
* ``GET  /api/v1/campaigns/{campaign_id}/imports/``: the campaign's import history (READ).

A batch (or campaign) of a client the user is not a member of is a 404.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.db.models import Count, Q
from django.http import Http404, StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import APIException
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.core.permissions import ClientScopedViewSet
from apps.core.roles import Level
from apps.core.schema import error_responses
from apps.imports import csv_import, error_report
from apps.imports.models import ImportBatch, ImportRow, ImportRowOutcome

from .batch_serializers import (
    TOP_ERROR_CODES,
    ErrorReportQuerySerializer,
    ImportBatchDetailSerializer,
    ImportBatchListQuerySerializer,
    ImportRowListQuerySerializer,
    ImportRowSerializer,
    batch_summary,
)
from .csv_serializers import ImportBatchSerializer

TAGS = ["imports"]


def _levels(level: str, who: str) -> str:
    return (
        f"Needs the `{level}` level: {who}. Others get 403; batches of clients you are not a "
        "member of give 404."
    )


class BatchAlreadyFinished(APIException):
    """409: the batch already ended, so there is nothing left to cancel."""

    status_code = 409
    default_code = "batch_already_finished"
    default_detail = "This import has already finished and cannot be cancelled."


class ImportBatchViewSet(ClientScopedViewSet):
    queryset = ImportBatch.objects.select_related("campaign", "job")
    serializer_class = ImportBatchSerializer
    lookup_url_kwarg = "batch_id"
    action_levels: ClassVar[dict[str, Level]] = {
        "retrieve": Level.READ,
        "rows": Level.READ,
        "errors_csv": Level.READ,
        "cancel": Level.EDIT,
    }

    # ---------------------------------------------------------------- status

    @extend_schema(
        tags=TAGS,
        operation_id="imports_retrieve",
        summary="Get an import batch (status)",
        description="The batch the `status_url` of an import answers: `status` (`pending`, "
        "`processing`, `completed`, `partial`, `failed`, `cancelled`), the counters "
        "(`total_count`, one per outcome, `processed_count`), `progress_percent` (processed "
        "over total), the background `job` and its `job_status`, `error_summary` (why a whole "
        "batch failed or was cancelled) and a `summary` with the outcome counts, the most "
        "frequent error codes and the number of weak duplicates, so a result screen needs no "
        "paging through rows. Poll it while `status` is `pending` or `processing`. "
        + _levels("READ", "any member of the batch's client"),
        responses={200: ImportBatchDetailSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    def retrieve(self, request: Request, batch_id: str | None = None) -> Response:
        batch = self.get_object()
        rows = ImportRow.objects.filter(batch=batch)
        codes = (
            rows.exclude(error_code="")
            .values("error_code")
            .annotate(count=Count("id"))
            .order_by("-count", "error_code")[:TOP_ERROR_CODES]
        )
        weak = rows.filter(outcome=ImportRowOutcome.CREATED, match_strength="weak").count()
        batch.summary = batch_summary(
            batch, [{"code": c["error_code"], "count": c["count"]} for c in codes], weak
        )
        batch.job_status = batch.job.status if batch.job else None
        return Response(ImportBatchDetailSerializer(batch).data)

    # ---------------------------------------------------------------- rows

    @extend_schema(
        tags=TAGS,
        operation_id="imports_rows_list",
        summary="List the row results of an import batch",
        description="One entry per submitted row, paginated (`page`, `page_size` up to 100), in "
        "file order (`ordering=-row_number` reverses). Filters: `outcome`, `has_error`, "
        "`error_code`, `match_strength`, `search` (name or domain). Each row has the entered "
        "and normalized fields, the `error_code`/`error_message`, the duplicate match "
        "(`match_strength`, `matched_on`, `candidate`), the `company` it created or matched, "
        "and `raw_data` as stored (scrubbed and shortened). Rows appear while the batch is "
        "still processing. " + _levels("READ", "any member of the batch's client"),
        parameters=[ImportRowListQuerySerializer],
        responses={
            200: ImportRowSerializer(many=True),
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    def rows(self, request: Request, batch_id: str | None = None) -> Response:
        batch = self.get_object()
        query = ImportRowListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        queryset = ImportRow.objects.filter(batch=batch).select_related("company", "candidate")
        if "outcome" in params:
            queryset = queryset.filter(outcome=params["outcome"])
        if "has_error" in params:
            queryset = (
                queryset.exclude(error_code="")
                if params["has_error"] == "true"
                else queryset.filter(error_code="")
            )
        if "error_code" in params:
            queryset = queryset.filter(error_code=params["error_code"])
        if "match_strength" in params:
            queryset = queryset.filter(match_strength=params["match_strength"])
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(Q(name__icontains=term) | Q(domain__icontains=term))
        queryset = queryset.order_by(params.get("ordering", "row_number"))
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(ImportRowSerializer(page, many=True).data)

    # ---------------------------------------------------------------- error report

    @extend_schema(
        tags=TAGS,
        operation_id="imports_errors_csv",
        summary="Download the error report of an import batch",
        description="A CSV of the rows that failed or were skipped (`?include=duplicates` adds "
        "the rows that matched an existing company), in file order, streamed. Columns: "
        "`row_number, name, website, profile_url, country` (as entered), `outcome, "
        "error_code, error_message`. UTF-8 with BOM; every cell is protected against "
        "spreadsheet formulas (a value starting with `=`, `+`, `-` or `@` gets a leading `'`). "
        "Fix the rows and upload the file again: the importer reads the company columns and "
        "ignores the others. Available while the batch runs (the report so far) and after. "
        + _levels("READ", "any member of the batch's client"),
        parameters=[ErrorReportQuerySerializer],
        responses={
            (200, "text/csv"): OpenApiTypes.BINARY,
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    def errors_csv(self, request: Request, batch_id: str | None = None) -> StreamingHttpResponse:
        batch = self.get_object()
        query = ErrorReportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        response = StreamingHttpResponse(
            error_report.iter_error_report(
                batch, include_duplicates=query.validated_data.get("include") == "duplicates"
            ),
            content_type=error_report.REPORT_CONTENT_TYPE,
        )
        filename = error_report.report_filename(batch)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    # ---------------------------------------------------------------- cancel

    @extend_schema(
        tags=TAGS,
        operation_id="imports_cancel",
        summary="Cancel an import batch",
        description="A `pending` or `processing` batch becomes `cancelled`: the worker stops at "
        "its next chunk (at most one chunk of rows, 100 by default, is still processed), the "
        "stored upload is deleted, and rows already imported stay (their companies exist). "
        "Idempotent: cancelling a cancelled batch returns it again (200). A batch that "
        "ended any other way is 409 `batch_already_finished`. Needs the `EDIT` level: manager "
        "and admin. Others get 403; batches of clients you are not a member of give 404.",
        request=None,
        responses={200: ImportBatchSerializer, **error_responses(401, 403, 404, 409, 429, 500)},
    )
    def cancel(self, request: Request, batch_id: str | None = None) -> Response:
        batch = self.get_object()
        try:
            batch = csv_import.cancel_csv_import(batch, cast(User, request.user))
        except csv_import.BatchNotCancellableError as exc:
            raise BatchAlreadyFinished from exc
        return Response(ImportBatchSerializer(batch).data)


class CampaignImportBatchViewSet(ClientScopedViewSet):
    """``GET /campaigns/{campaign_id}/imports/``: the campaign's import history."""

    queryset = ImportBatch.objects.all()
    serializer_class = ImportBatchSerializer
    lookup_url_kwarg = "campaign_id"
    action_levels: ClassVar[dict[str, Level]] = {"list": Level.READ}

    @extend_schema(
        tags=TAGS,
        operation_id="campaign_imports_list",
        summary="List a campaign's import history",
        description="Every import batch of the campaign (manual entries, CSV uploads, provider "
        "imports), newest first, paginated. Filters: `source`, `status`, `created_by`, "
        "`created_from` / `created_to` (dates, inclusive); `ordering` (`created_at`, "
        "`finished_at`, `total_count`, `failed_count`, each optionally with `-`). Each entry is "
        "the batch with its counters and progress; open `GET /imports/{id}/` for the summary. "
        "Needs the `READ` level: any member of the campaign's client. Others get 403; "
        "campaigns of clients you are not a member of give 404.",
        parameters=[ImportBatchListQuerySerializer],
        responses={
            200: ImportBatchSerializer(many=True),
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    def list(self, request: Request, campaign_id: str | None = None) -> Response:
        try:
            campaign = Campaign.objects.for_user(request.user).get(pk=self.kwargs["campaign_id"])
        except Campaign.DoesNotExist as exc:
            raise Http404 from exc
        query = ImportBatchListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params: dict[str, Any] = query.validated_data
        queryset = self.get_queryset().filter(campaign=campaign)
        for name in ("source", "status"):
            if name in params:
                queryset = queryset.filter(**{name: params[name]})
        if "created_by" in params:
            queryset = queryset.filter(created_by_id=params["created_by"])
        if "created_from" in params:
            queryset = queryset.filter(created_at__date__gte=params["created_from"])
        if "created_to" in params:
            queryset = queryset.filter(created_at__date__lte=params["created_to"])
        ordering = params.get("ordering", "-created_at")
        queryset = queryset.order_by(ordering, "-id" if ordering.startswith("-") else "id")
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(ImportBatchSerializer(page, many=True).data)
