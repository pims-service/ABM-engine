"""Typed errors for company search providers (issue #66).

Every error has a stable ``code`` (clients and the import error report may rely on it) and a
fixed, safe ``message``. The message is chosen by the class, never built from upstream text, so a
credential, a URL with a token or a raw vendor response body cannot leak through ``str(error)``,
logs or an API response. Adapters keep the vendor detail in the exception chain
(``raise ProviderUnavailable(...) from exc``), which this module never prints.

    ProviderError
      ProviderAuthError           code "provider_auth"           not retryable (fix credentials)
      ProviderRateLimited         code "provider_rate_limited"   retryable after ``retry_after`` s
      ProviderUnavailable         code "provider_unavailable"    retryable (timeout, 5xx, network)
      ProviderBadRequest          code "provider_bad_request"    not retryable (request is wrong)
      ProviderQuotaExceeded       code "provider_quota_exceeded" not retryable until credits reset
"""

from __future__ import annotations

import re

# The only free text an error may carry is a field name of OUR request, in this shape.
_SAFE_FIELD = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


def _safe_name(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_NAME.fullmatch(value))


class ProviderError(Exception):
    """Base of every error a ``CompanySearchProvider`` may raise from ``search``."""

    code = "provider_error"
    message = "The data provider returned an error."
    retryable = False

    def __init__(self, *, provider_name: str = "", upstream_status: int | None = None) -> None:
        super().__init__(self.message)
        self.provider_name = provider_name if _safe_name(provider_name) else ""
        # An HTTP status is a number, safe to keep for diagnostics.
        self.upstream_status = upstream_status if isinstance(upstream_status, int) else None

    def __str__(self) -> str:
        return str(self.args[0])

    def as_dict(self) -> dict[str, object]:
        """The safe, serializable form (for ``ImportBatch.error_summary`` or an API error)."""
        return {
            "code": self.code,
            "message": str(self),
            "provider": self.provider_name,
            "retryable": self.retryable,
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
        super().__init__(provider_name=provider_name, upstream_status=upstream_status)
        # Seconds to wait; ``None`` when the provider did not say. Never negative.
        self.retry_after: float | None = (
            max(0.0, float(retry_after))
            if isinstance(retry_after, int | float) and not isinstance(retry_after, bool)
            else None
        )

    def as_dict(self) -> dict[str, object]:
        return {**super().as_dict(), "retry_after": self.retry_after}


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    message = "The data provider is not available right now."
    retryable = True


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


class ProviderRegistryError(Exception):
    """A registry problem (not an upstream one): a refused registration or an unknown provider."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
