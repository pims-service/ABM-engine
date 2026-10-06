"""Request-ID middleware: correlate every log line and response with one ID."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .logging import reset_request_id, set_request_id

REQUEST_ID_HEADER = "X-Request-ID"
# Inbound IDs are echoed into logs and headers, so only accept a safe, bounded charset.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

access_logger = logging.getLogger("apps.core.access")


def resolve_request_id(request: HttpRequest) -> str:
    """Use the inbound ``X-Request-ID`` when it is well-formed, otherwise generate one."""
    inbound = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if _VALID_REQUEST_ID.fullmatch(inbound):
        return inbound
    return uuid.uuid4().hex


def _level_for(status: int) -> int:
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO


class RequestIDMiddleware:
    """Bind a request ID for the duration of the request and return it as ``X-Request-ID``.

    Place it first in ``MIDDLEWARE`` so even redirects and security responses carry the header.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = resolve_request_id(request)
        request.request_id = request_id  # type: ignore[attr-defined]
        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = request_id
            access_logger.log(
                _level_for(response.status_code),
                "%s %s -> %s",
                request.method,
                request.path,
                response.status_code,
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return response
        finally:
            reset_request_id(token)
