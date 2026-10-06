"""Standard API error envelope.

Every error response from the API has this shape::

    {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}

``code`` is a stable machine-readable string, ``message`` a human-readable summary, ``details``
extra structured data (field errors for validation, ``retry_after`` for throttling, else null).
Unexpected exceptions never leak internals: the client gets a generic message and the full
traceback goes to the log together with the request ID.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.views import defaults
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .logging import get_request_id

logger = logging.getLogger(__name__)

GENERIC_SERVER_ERROR = "An unexpected error occurred. Please try again later."

# Exception type -> stable error code. Order matters: first isinstance match wins.
_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (exceptions.ValidationError, "validation_error"),
    (exceptions.ParseError, "parse_error"),
    (exceptions.NotAuthenticated, "not_authenticated"),
    (exceptions.AuthenticationFailed, "authentication_failed"),
    (exceptions.PermissionDenied, "permission_denied"),
    (DjangoPermissionDenied, "permission_denied"),
    (exceptions.NotFound, "not_found"),
    (Http404, "not_found"),
    (exceptions.MethodNotAllowed, "method_not_allowed"),
    (exceptions.NotAcceptable, "not_acceptable"),
    (exceptions.UnsupportedMediaType, "unsupported_media_type"),
    (exceptions.Throttled, "throttled"),
)


def error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": get_request_id(),
        }
    }


def _jsonable(value: Any) -> Any:
    """Turn DRF ``ErrorDetail`` structures into plain str / list / dict."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return str(value)


def _code_for(exc: Exception) -> str:
    for exc_type, code in _CODES:
        if isinstance(exc, exc_type):
            return code
    if isinstance(exc, exceptions.APIException):
        return str(exc.default_code)
    return "error"


def _message_for(exc: Exception, code: str) -> str:
    if code == "validation_error":
        return "Request validation failed."
    if isinstance(exc, exceptions.APIException):
        return str(exc.detail) if isinstance(exc.detail, str) else str(exc.default_detail)
    return "Not found." if code == "not_found" else "Permission denied."


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    """DRF ``EXCEPTION_HANDLER``: wraps every error in the standard envelope."""
    response = drf_exception_handler(exc, context)  # sets headers and rolls back atomic requests

    if response is None:
        # Unhandled exception: log everything, tell the client nothing.
        view = context.get("view")
        logger.error(
            "Unhandled exception in %s",
            type(view).__name__ if view is not None else "unknown view",
            exc_info=exc,
        )
        # Dev only: name the exception type; never the message or traceback.
        debug_details = {"exception": type(exc).__name__} if settings.DEBUG else None
        return Response(
            error_payload("internal_error", GENERIC_SERVER_ERROR, debug_details),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = _code_for(exc)
    details: Any = None
    if isinstance(exc, exceptions.ValidationError):
        details = _jsonable(exc.detail)
    elif isinstance(exc, exceptions.Throttled):
        # The DRF stubs omit ``Throttled.wait``.
        wait = getattr(exc, "wait", None)
        details = {"retry_after": int(wait)} if wait is not None else None
    response.data = error_payload(code, _message_for(exc, code), details)
    return response


# ------------------------------------------------- non-DRF 404/500 for /api/ paths
def _is_api_request(request: HttpRequest) -> bool:
    return request.path.startswith("/api/")


def not_found_view(
    request: HttpRequest, exception: Exception, *args: Any, **kwargs: Any
) -> HttpResponse:
    """``handler404``: JSON envelope under ``/api/`` (e.g. unknown route), HTML elsewhere."""
    if _is_api_request(request):
        return JsonResponse(error_payload("not_found", "Not found."), status=404)
    return defaults.page_not_found(request, exception, *args, **kwargs)


def server_error_view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
    """``handler500``: generic JSON envelope under ``/api/``, Django's plain page elsewhere."""
    if _is_api_request(request):
        return JsonResponse(error_payload("internal_error", GENERIC_SERVER_ERROR), status=500)
    return defaults.server_error(request, *args, **kwargs)
