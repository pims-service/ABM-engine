"""OpenAPI schema helpers (drf-spectacular): error envelope components and the docs gate.

The committed schema lives in `docs/api/openapi.yaml`; regenerate it with
`make api-schema` (see backend/README.md, "OpenAPI schema and API client").
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponseBase
from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers

ViewFunc = Callable[..., HttpResponseBase]


class ErrorBodySerializer(serializers.Serializer[dict[str, Any]]):
    """The `error` object of the standard envelope (apps/core/exceptions.py)."""

    code = serializers.CharField(help_text="Stable machine-readable code, e.g. `validation_error`.")
    message = serializers.CharField(help_text="Human-readable summary; may change, do not parse.")
    details = serializers.JSONField(
        allow_null=True,
        help_text="Field errors for `validation_error`, `{retry_after}` for `throttled`, "
        "else null.",
    )
    request_id = serializers.CharField(
        allow_null=True, help_text="Value of the `X-Request-ID` response header."
    )


class ErrorEnvelopeSerializer(serializers.Serializer[dict[str, Any]]):
    """Every error response of the API has this body."""

    error = ErrorBodySerializer()


_DESCRIPTIONS = {
    400: "Validation or parse error (`validation_error`, `parse_error`).",
    401: "Missing, invalid or expired credentials (`not_authenticated`, `authentication_failed`).",
    403: "Not allowed (`permission_denied`).",
    404: "Not found (`not_found`).",
    429: "Throttled (`throttled`, with `details.retry_after`).",
    500: "Unexpected server error (`internal_error`).",
}


def error_responses(*codes: int) -> dict[int | tuple[int, str], OpenApiResponse]:
    """`responses=` entries for `extend_schema`: the error envelope for each status code."""
    return {
        code: OpenApiResponse(response=ErrorEnvelopeSerializer, description=_DESCRIPTIONS[code])
        for code in codes
    }


def docs_enabled_only(view: ViewFunc) -> ViewFunc:
    """Serve `view` only while `settings.API_DOCS_ENABLED`; otherwise a plain 404 envelope.

    The flag is read per request so tests can switch it; production keeps it off.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not settings.API_DOCS_ENABLED:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper
