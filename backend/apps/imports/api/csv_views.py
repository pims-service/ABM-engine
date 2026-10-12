"""CSV import endpoints (issue #62).

* ``GET  /api/v1/imports/csv-template/``: the downloadable template (any signed-in user).
* ``POST /api/v1/campaigns/{campaign_id}/csv-import/preview/``: validate an upload and show what
  the import would do. Stores nothing. EDIT level.
* ``POST /api/v1/campaigns/{campaign_id}/csv-import/``: accept the upload, queue the background
  import, answer 202. EDIT level.
* ``POST /api/v1/imports/{batch_id}/cancel/``: ``batches.py``.

Built on ``ClientScopedViewSet`` (docs/permissions.md): a campaign of another client is a 404,
never a 403. The work itself is in ``apps/imports/csv_import.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.core.permissions import ClientScopedViewSet
from apps.core.roles import Level
from apps.core.schema import error_responses
from apps.imports import csv_import
from apps.imports.csvparse import (
    FIELDS,
    FILE_TOO_LARGE,
    TEMPLATE_CONTENT_TYPE,
    TEMPLATE_FILENAME,
    CsvFileError,
    Mapping,
    MappingError,
    build_template_csv,
)
from apps.imports.models import ImportBatch, ImportSource
from apps.imports.schema import ROW_FIELD

from .csv_serializers import (
    CsvImportAcceptedSerializer,
    CsvPreviewSerializer,
    CsvUploadSerializer,
    ImportBatchSerializer,
)

TAGS = ["imports"]

#: Room for the multipart framing and the optional mapping field on top of the file itself.
MULTIPART_OVERHEAD = 256 * 1024


def batch_status_url(batch: ImportBatch) -> str:
    return f"/api/v1/imports/{batch.pk}/"


def _levels(level: str, who: str) -> str:
    return (
        f"Needs the `{level}` level: {who}. Others get 403; campaigns of clients you are not a "
        "member of give 404."
    )


class CsvRejected(APIException):
    """The file (or the mapping) cannot be imported. ``error.code`` is the csvparse code
    (``empty_file``, ``binary_file``, ``too_many_rows``, ``name_column_missing``, ...), the status
    is 413 for ``file_too_large`` and 400 otherwise, ``details.issues`` lists every problem."""

    def __init__(self, error: CsvFileError) -> None:
        issues = error.issues if isinstance(error, MappingError) else [error.as_issue()]
        self.status_code = 413 if error.code == FILE_TOO_LARGE else 400
        self.default_code = error.code
        self.error_details = {"issues": [issue.as_dict() for issue in issues]}
        super().__init__(detail=str(error), code=error.code)


class ImportInProgress(APIException):
    """409: the same file is already being imported into this campaign."""

    status_code = 409
    default_code = "import_in_progress"
    default_detail = (
        "This file is already being imported into this campaign. Wait for it to finish or "
        "cancel it first."
    )

    def __init__(self, batch: ImportBatch) -> None:
        self.error_details = {
            "batch": dict(ImportBatchSerializer(batch).data),
            "status_url": batch_status_url(batch),
        }
        super().__init__()


@contextmanager
def api_errors() -> Iterator[None]:
    """Parser and service errors as API errors."""
    try:
        yield
    except CsvFileError as exc:
        raise CsvRejected(exc) from exc
    except csv_import.DuplicateImportError as exc:
        raise ImportInProgress(exc.batch) from exc
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise ValidationError(exc.message_dict) from exc
        raise ValidationError({"non_field_errors": exc.messages}) from exc


# ------------------------------------------------------------------ template


class CsvTemplateView(APIView):
    """Static download: it carries no client data, so it needs a login but no membership."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        tags=TAGS,
        operation_id="imports_csv_template",
        summary="Download the CSV template",
        description="The CSV file customers fill in: header `company_name, website, profile_url, "
        "country, industry, notes`, UTF-8 with BOM (Excel shows Arabic correctly). With "
        "`examples=true` it holds three placeholder rows. Any signed-in user may download it.",
        parameters=[
            OpenApiParameter(
                "examples",
                OpenApiTypes.BOOL,
                description="Add three placeholder rows. Default false (header only).",
            )
        ],
        responses={
            (200, "text/csv"): OpenApiTypes.BINARY,
            **error_responses(401, 429, 500),
        },
    )
    def get(self, request: Request) -> HttpResponse:
        examples = request.query_params.get("examples", "").lower() in {"1", "true", "yes"}
        response = HttpResponse(
            build_template_csv(include_examples=examples), content_type=TEMPLATE_CONTENT_TYPE
        )
        response["Content-Disposition"] = f'attachment; filename="{TEMPLATE_FILENAME}"'
        response["Cache-Control"] = "private, max-age=3600"
        return response


# ------------------------------------------------------------------ upload


def _issue_dicts(issues: Any) -> list[dict[str, str]]:
    return [{"field": "", **issue.as_dict()} for issue in issues]


class CsvImportViewSet(ClientScopedViewSet):
    """Preview and start a CSV import into one campaign."""

    queryset = Campaign.objects.select_related("client")
    lookup_url_kwarg = "campaign_id"
    parser_classes = (MultiPartParser,)
    action_levels: ClassVar[dict[str, Level]] = {
        "preview": Level.EDIT,
        "create": Level.EDIT,
    }

    def _user(self) -> User:
        return cast(User, self.request.user)

    def _read_upload(self, request: Request) -> tuple[Any, Mapping | None]:
        """The uploaded file and the optional mapping, after the size is checked."""
        limit = csv_import.csv_limits().max_file_bytes
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > limit + MULTIPART_OVERHEAD:  # refuse before reading the body
            raise CsvRejected(CsvFileError(FILE_TOO_LARGE, f"limit {limit} bytes"))
        serializer = CsvUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with api_errors():
            mapping = csv_import.parse_mapping(data.get("mapping"))
        return data["file"], mapping

    # ---------------------------------------------------------------- preview

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_csv_import_preview",
        summary="Preview a CSV upload",
        description="Reads the whole file (nothing is stored or created) and returns what the "
        "mapping screen needs: the headers, the mapping suggested from them, the first rows "
        "with the problems the import would record, totals, warnings (e.g. a legacy "
        "encoding), the file's SHA-256 and the limits. Whole-file problems are 400 with a "
        "stable `error.code` (`empty_file`, `no_header`, `no_data_rows`, `binary_file`, "
        "`unsupported_format`, `undecodable`, `too_many_columns`, `header_too_long`, "
        "`too_many_rows` (over 5000), `name_column_missing`, `unknown_column`, `unknown_field`, "
        "`duplicate_target`); a file over the size limit (10 MiB) is 413 `file_too_large`. "
        "`details.issues` lists every problem. The optional `mapping` field is applied to the "
        "rows shown. " + _levels("EDIT", "manager and admin"),
        request={"multipart/form-data": CsvUploadSerializer},
        responses={200: CsvPreviewSerializer, **error_responses(400, 401, 403, 404, 413, 429, 500)},
    )
    def preview(self, request: Request, campaign_id: str | None = None) -> Response:
        campaign = self.get_object()
        upload, mapping = self._read_upload(request)
        with api_errors():
            result = csv_import.preview_csv(upload, mapping)
        summary = result.summary
        rows = []
        for row in result.rows:
            errors = _issue_dicts(row.errors)
            checked = result.row_errors.get(row.row_number)
            if checked is not None:
                errors += [
                    {
                        "field": "" if e.field == ROW_FIELD else e.field,
                        "code": e.code,
                        "message": e.message,
                        "detail": "",
                    }
                    for e in checked.errors
                ]
            rows.append(
                {
                    "row_number": row.row_number,
                    "raw": row.raw,
                    "mapped": row.mapped,
                    "errors": errors,
                }
            )
        active = (
            ImportBatch.objects.active()
            .filter(campaign=campaign, source=ImportSource.CSV, file_sha256=summary.file_sha256)
            .values_list("pk", flat=True)
            .first()
        )
        limits = result.limits
        body = {
            "file": {
                "name": getattr(upload, "name", "") or "",
                "size_bytes": summary.file_size,
                "sha256": summary.file_sha256,
                "encoding": summary.encoding,
                "delimiter": summary.delimiter,
            },
            "headers": result.headers,
            "suggested_mapping": result.suggested.to_dict(),
            "mapping": result.mapping.to_dict(),
            "unmapped_headers": list(result.mapping.unmapped),
            "fuzzy_headers": list(result.suggested.fuzzy),
            "company_fields": list(FIELDS),
            "rows": rows,
            "total_rows": summary.total_rows,
            "blank_rows": summary.blank_rows,
            "error_rows": summary.error_rows,
            "warnings": _issue_dicts(summary.warnings),
            "limits": {
                "max_file_bytes": limits.max_file_bytes,
                "max_rows": limits.max_rows,
                "max_columns": limits.max_columns,
                "max_cell_length": limits.max_cell_length,
            },
            "active_import": active,
        }
        return Response(CsvPreviewSerializer(body).data)

    # ---------------------------------------------------------------- import

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_csv_import_create",
        summary="Start a CSV import",
        description="Validates the whole file (same rules and errors as the preview), stores it "
        "privately, creates the import batch (source `csv`) and a background job, and "
        "returns 202 at once; rows are processed by the worker in chunks. Poll "
        "`status_url` (the batch: `status`, counters, `progress_percent`) until `status` is "
        "`completed`, `partial` (some rows failed), `failed` or `cancelled`. Each row is "
        "validated and de-duplicated like a manual entry; a bad row is recorded as failed "
        "and never stops the batch. Re-uploading the same file (SHA-256) into the same "
        "campaign while an earlier batch of it is pending or processing is 409 "
        "`import_in_progress` with the existing batch in `details.batch`; once it ended the "
        "same file may be imported again (rows then come back as duplicates). The optional "
        "`mapping` field is `{file column: field}` JSON; left out, the suggestion is used. "
        "400 for an archived campaign or client. " + _levels("EDIT", "manager and admin"),
        request={"multipart/form-data": CsvUploadSerializer},
        responses={
            202: CsvImportAcceptedSerializer,
            **error_responses(400, 401, 403, 404, 409, 413, 429, 500),
        },
    )
    def create(self, request: Request, campaign_id: str | None = None) -> Response:
        campaign = self.get_object()
        upload, mapping = self._read_upload(request)
        with api_errors():
            batch, job = csv_import.submit_csv_import(
                campaign,
                self._user(),
                upload,
                filename=getattr(upload, "name", "") or "",
                mapping=mapping,
            )
        body = {"batch": batch, "job_id": job.pk, "status_url": batch_status_url(batch)}
        response = Response(CsvImportAcceptedSerializer(body).data, status=202)
        response["Location"] = batch_status_url(batch)
        return response
