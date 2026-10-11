"""Typed errors shared by every integration category (issues #66 and #75, ADR 0013).

Every error has a stable ``code`` (clients, job retry policy and the import error report may rely
on it), a ``retryable`` flag and a fixed, safe ``message``. The message is chosen by the class,
never built from upstream text, so a credential, a URL with a token or a raw vendor response body
cannot leak through ``str(error)``, logs or an API response. Adapters keep the vendor detail in the
exception chain (``raise ProviderUnavailable(...) from exc``), which this module never prints.

    ProviderError
      ProviderAuthError           "provider_auth"            no retry (fix credentials)
      ProviderRateLimited         "provider_rate_limited"    retry after ``retry_after`` seconds
      ProviderUnavailable         "provider_unavailable"     retry with backoff (5xx, network)
        ProviderTimeout           "provider_timeout"         retry with backoff
      ProviderBadRequest          "provider_bad_request"     no retry (our request is wrong)
      ProviderQuotaExceeded       "provider_quota_exceeded"  no retry until credits reset
      ProviderNotConfigured       "provider_not_configured"  no retry (settings or credentials)
      ProviderNotFound            "provider_not_found"       no retry (unknown provider/record)
      ProviderConflict            "provider_conflict"        no retry (state conflict, 409)
      ProviderUpstreamError       "provider_upstream_error"  no retry (unusable upstream answer)
        ProviderInvalidOutput     "provider_invalid_output"  no retry here (LLM repair is #81)

``ProviderRegistryError`` is not an upstream error: it reports a refused registration.
``map_http_status`` is the one table from an HTTP status to these classes.
"""

from __future__ import annotations

import re

# The only free text an error may carry is a field name of OUR request, in this shape.
_SAFE_FIELD = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60


def _safe_name(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_NAME.fullmatch(value))


def _clean_seconds(value: object) -> float | None:
    """Seconds to wait as a non-negative, bounded float; ``None`` when absent or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value != value:  # NaN
        return None
    return min(max(0.0, float(value)), float(MAX_RETRY_AFTER_SECONDS))


class ProviderError(Exception):
    """Base of every error an adapter may raise from an operation."""

    code = "provider_error"
    message = "The data provider returned an error."
    retryable = False

    def __init__(
        self,
        *,
        provider_name: str = "",
        upstream_status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(self.message)
        self.provider_name = provider_name if _safe_name(provider_name) else ""
        # An HTTP status is a number, safe to keep for diagnostics.
        self.upstream_status = (
            upstream_status
            if isinstance(upstream_status, int) and not isinstance(upstream_status, bool)
            else None
        )
        # Seconds to wait before a retry, when the provider said; only meaningful if retryable.
        self.retry_after: float | None = _clean_seconds(retry_after) if self.retryable else None

    def __str__(self) -> str:
        return str(self.args[0])

    def as_dict(self) -> dict[str, object]:
        """The safe, serializable form (for ``ImportBatch.error_summary``, a job row, an API
        error or a log line). Keys are stable; values are never upstream text."""
        return {
            "code": self.code,
            "message": str(self),
            "provider": self.provider_name,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
        }


class ProviderAuthError(ProviderError):
    code = "provider_auth"
    message = "The data provider rejected the credentials. Check the integration settings."


class ProviderRateLimited(ProviderError):
    code = "provider_rate_limited"
    message = "The data provider is rate limiting requests. Try again later."
    retryable = True

    def __init__(
        self,
        retry_after: float | None = None,
        *,
        provider_name: str = "",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(
            provider_name=provider_name, upstream_status=upstream_status, retry_after=retry_after
        )


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    message = "The data provider is not available right now."
    retryable = True


class ProviderTimeout(ProviderUnavailable):
    code = "provider_timeout"
    message = "The data provider did not answer in time."


class ProviderBadRequest(ProviderError):
    code = "provider_bad_request"
    message = "The search request is not valid for this provider."

    def __init__(
        self,
        field: str = "",
        *,
        provider_name: str = "",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, upstream_status=upstream_status)
        # Name of the offending field of OUR request; anything else is dropped.
        self.field = field if isinstance(field, str) and _SAFE_FIELD.match(field) else ""
        if self.field:
            self.args = (f"{self.message} Field: {self.field}.",)

    def as_dict(self) -> dict[str, object]:
        return {**super().as_dict(), "field": self.field}


class ProviderQuotaExceeded(ProviderError):
    code = "provider_quota_exceeded"
    message = "The data provider quota or credits are used up."


class ProviderNotConfigured(ProviderError):
    """The integration lacks settings or credentials. ``fields`` names them (names only)."""

    code = "provider_not_configured"
    message = "The integration is not configured. Add the required settings and credentials."

    def __init__(
        self,
        fields: tuple[str, ...] | list[str] = (),
        *,
        provider_name: str = "",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, upstream_status=upstream_status)
        self.fields: tuple[str, ...] = tuple(
            f for f in fields if isinstance(f, str) and _SAFE_FIELD.match(f)
        )[:50]

    def as_dict(self) -> dict[str, object]:
        return {**super().as_dict(), "fields": list(self.fields)}


class ProviderNotFound(ProviderError):
    """An unknown provider, or a record the provider does not have (HTTP 404)."""

    code = "provider_not_found"
    message = "The requested provider or record was not found."


class ProviderConflict(ProviderError):
    """The upstream state conflicts with the request (HTTP 409), for example a duplicate."""

    code = "provider_conflict"
    message = "The request conflicts with the current state at the provider."


class ProviderUpstreamError(ProviderError):
    """The provider answered, but not with something usable (malformed body, unexpected status)."""

    code = "provider_upstream_error"
    message = "The data provider returned an unexpected answer."


class ProviderInvalidOutput(ProviderUpstreamError):
    """An LLM answer that does not validate against the requested schema (#81)."""

    code = "provider_invalid_output"
    message = "The model output did not match the expected structure."


class ProviderRegistryError(Exception):
    """A registry problem (not an upstream one): a refused registration or an unknown provider."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EnvelopeError(ValueError):
    """An adapter built a result that breaks the envelope rules (no provenance, naive datetime,
    over a size cap). A bug in the adapter, not an upstream condition: ``execute`` turns it into
    ``ProviderUpstreamError`` so callers still only see ``ProviderError``. The message names the
    rule and field, never data."""


ALL_ERROR_CLASSES: tuple[type[ProviderError], ...] = (
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUnavailable,
    ProviderTimeout,
    ProviderBadRequest,
    ProviderQuotaExceeded,
    ProviderNotConfigured,
    ProviderNotFound,
    ProviderConflict,
    ProviderUpstreamError,
    ProviderInvalidOutput,
)


def parse_retry_after(value: object) -> float | None:
    """A ``Retry-After`` header value in seconds, or ``None`` (absent, an HTTP date, garbage)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _clean_seconds(value)
    if isinstance(value, str) and re.fullmatch(r"\s*\d{1,9}(\.\d{1,3})?\s*", value):
        return _clean_seconds(float(value))
    return None


def error_class_for_status(status: int) -> type[ProviderError]:
    """The error class for an HTTP status from a provider (see ``map_http_status``)."""
    if status in (401, 403):
        return ProviderAuthError
    if status == 402:
        return ProviderQuotaExceeded
    if status in (404, 410):
        return ProviderNotFound
    if status in (408, 504):
        return ProviderTimeout
    if status == 409:
        return ProviderConflict
    if status == 429:
        return ProviderRateLimited
    if status in (400, 405, 413, 414, 415, 422):
        return ProviderBadRequest
    if 500 <= status <= 599:
        return ProviderUnavailable
    return ProviderUpstreamError


def map_http_status(
    status: int,
    *,
    provider_name: str = "",
    retry_after: float | str | None = None,
) -> ProviderError:
    """Build the typed error for an HTTP ``status`` an adapter received.

    401/403 auth; 402 quota; 404/410 not found; 408/504 timeout; 409 conflict; 429 rate limited
    (``retry_after`` seconds or a ``Retry-After`` header value); 400/405/413/414/415/422 bad
    request; other 5xx unavailable; anything else (1xx, 3xx, other 4xx) an upstream error.
    The status is kept as a number; no upstream text is ever used.
    """
    cls = error_class_for_status(status) if isinstance(status, int) else ProviderUpstreamError
    seconds = parse_retry_after(retry_after)
    code = status if isinstance(status, int) else None
    if cls is ProviderRateLimited:
        return ProviderRateLimited(seconds, provider_name=provider_name, upstream_status=code)
    if cls is ProviderBadRequest:
        return ProviderBadRequest(provider_name=provider_name, upstream_status=code)
    if issubclass(cls, ProviderUnavailable):
        return cls(provider_name=provider_name, upstream_status=code, retry_after=seconds)
    return cls(provider_name=provider_name, upstream_status=code)
