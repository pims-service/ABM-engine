"""``/api/v1/imports/{batch_id}/cancel/``: stop an import batch (issue #62).

Built on ``ClientScopedViewSet`` (docs/permissions.md). Reading a batch's status and its error
report belong to issue #63.
"""

from __future__ import annotations

from typing import ClassVar, cast

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import APIException
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.core.permissions import ClientScopedViewSet
from apps.core.roles import Level
from apps.core.schema import error_responses
from apps.imports import csv_import
from apps.imports.models import ImportBatch

from .csv_serializers import ImportBatchSerializer

TAGS = ["imports"]


class BatchAlreadyFinished(APIException):
    """409: the batch already ended, so there is nothing left to cancel."""

    status_code = 409
    default_code = "batch_already_finished"
    default_detail = "This import has already finished and cannot be cancelled."


class ImportBatchViewSet(ClientScopedViewSet):
    queryset = ImportBatch.objects.select_related("campaign")
    serializer_class = ImportBatchSerializer
    lookup_url_kwarg = "batch_id"
    action_levels: ClassVar[dict[str, Level]] = {"cancel": Level.EDIT}

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
