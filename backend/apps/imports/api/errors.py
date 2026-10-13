"""Provider failures as stable API errors (issue #67).

The body is built only from the typed error's fixed ``code`` and ``message`` (never upstream
text, credentials or response bodies):

* ``ProviderRateLimited``    429 ``provider_rate_limited``, ``details.retry_after`` (seconds or
  null) and a ``Retry-After`` header when known;
* ``ProviderUnavailable``    503 ``provider_unavailable``;
* ``ProviderAuthError``      502 ``provider_auth`` (our credentials for the provider are wrong);
* ``ProviderQuotaExceeded``  502 ``provider_quota_exceeded``;
* ``ProviderBadRequest``     400 ``provider_bad_request`` (``details.field`` when known);
* any other ``ProviderError`` 502 ``provider_error``.
"""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import APIException

from apps.imports.providers import (
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

_STATUS: tuple[tuple[type[ProviderError], int], ...] = (
    (ProviderRateLimited, 429),
    (ProviderUnavailable, 503),
    (ProviderBadRequest, 400),
)


class ProviderAPIError(APIException):
    """An upstream provider problem, safe to show."""

    status_code = 502
    default_code = "provider_error"

    def __init__(self, error: ProviderError) -> None:
        super().__init__(detail=str(error), code=error.code)
        self.default_code = error.code
        for kind, status in _STATUS:
            if isinstance(error, kind):
                self.status_code = status
                break
        details: dict[str, Any] = {"provider": error.provider_name, "retryable": error.retryable}
        if isinstance(error, ProviderRateLimited):
            details["retry_after"] = None if error.retry_after is None else int(error.retry_after)
            if error.retry_after:
                self.wait = int(error.retry_after) + (0 if error.retry_after % 1 == 0 else 1)
        if isinstance(error, ProviderBadRequest) and error.field:
            details["field"] = error.field
        self.error_details = details
