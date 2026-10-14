"""Typed errors of the public website fetcher (issue #94).

All of them are ``ProviderError`` subclasses, so a job or an adapter that already handles the
framework family handles these too. Like the framework errors, the message is fixed per class and
never built from the URL, headers or the page: ``str(error)`` is safe to log or show. The only
extras are small, safe values: ``reason`` (a short constant from this module), ``status`` (a
number) and ``host`` (a validated, lower-case host name, never a full URL, never userinfo).

    ProviderError
      FetchError                      "fetch_error"
        FetchInvalidUrl               "fetch_invalid_url"          no retry
        FetchBlockedByPolicy          "fetch_blocked_policy"       no retry (denylist, Brief §4)
        FetchBlockedUnsafeTarget      "fetch_blocked_unsafe"       no retry (SSRF guard)
        FetchBlockedByRobots          "fetch_blocked_robots"       no retry (robots.txt)
        FetchOptedOut                 "fetch_opted_out"            no retry (noai/noindex)
        FetchTooLarge                 "fetch_too_large"            no retry
        FetchUnsupportedContent       "fetch_unsupported_content"  no retry
        FetchHttpError                "fetch_http_error"           retry only for 5xx and 429
        FetchTimeout (+ProviderTimeout) "fetch_timeout"            retry with backoff
        FetchNetworkError (+ProviderUnavailable) "fetch_network_error" retry with backoff
"""

from __future__ import annotations

import re
from typing import Final

from apps.integrations.framework.errors import (
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)

POLICY_REASON: Final = "no unauthorized scraping of social/professional networks (Brief §4)"

# Reasons of FetchBlockedUnsafeTarget (fixed vocabulary, safe to expose).
REASON_SCHEME: Final = "scheme_not_allowed"
REASON_USERINFO: Final = "userinfo_not_allowed"
REASON_PORT: Final = "port_not_allowed"
REASON_HOST: Final = "host_not_allowed"
REASON_PRIVATE_ADDRESS: Final = "private_address"
REASON_NO_ADDRESS: Final = "host_does_not_resolve"
REASON_REDIRECT: Final = "unsafe_redirect"
REASON_TOO_MANY_REDIRECTS: Final = "too_many_redirects"

_SAFE_HOST = re.compile(r"[a-z0-9.-]{1,253}")
_SAFE_REASON = re.compile(r"[a-z0-9_]{1,40}")


class FetchError(ProviderError):
    code = "fetch_error"
    message = "The website could not be fetched."

    def __init__(
        self,
        *,
        reason: str = "",
        host: str = "",
        upstream_status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(upstream_status=upstream_status, retry_after=retry_after)
        self.reason = reason if _SAFE_REASON.fullmatch(reason) else ""
        self.host = host if _SAFE_HOST.fullmatch(host) else ""

    def as_dict(self) -> dict[str, object]:
        return {**super().as_dict(), "reason": self.reason, "host": self.host}


class FetchInvalidUrl(FetchError):
    code = "fetch_invalid_url"
    message = "The address is not a valid public website URL."


class FetchBlockedByPolicy(FetchError):
    code = "fetch_blocked_policy"
    message = f"This site is never fetched: {POLICY_REASON}."


class FetchBlockedUnsafeTarget(FetchError):
    code = "fetch_blocked_unsafe"
    message = "The address points to a target the fetcher refuses to contact."


class FetchBlockedByRobots(FetchError):
    code = "fetch_blocked_robots"
    message = "The site's robots.txt does not allow this fetch."


class FetchOptedOut(FetchError):
    code = "fetch_opted_out"
    message = "The site opted out of automated use of its content (noai or noindex)."


class FetchTooLarge(FetchError):
    code = "fetch_too_large"
    message = "The response is larger than the allowed size."


class FetchUnsupportedContent(FetchError):
    code = "fetch_unsupported_content"
    message = "The response is not a supported text page."


class FetchHttpError(FetchError):
    """The site answered with an error status (4xx or 5xx, or a redirect without a target)."""

    code = "fetch_http_error"
    message = "The website answered with an error status."

    def __init__(self, status: int, *, host: str = "", retry_after: float | None = None) -> None:
        self.retryable = status == 429 or 500 <= status <= 599
        super().__init__(upstream_status=status, host=host, retry_after=retry_after)
        self.status: int = status


class FetchTimeout(FetchError, ProviderTimeout):
    code = "fetch_timeout"
    message = "The website did not answer in time."


class FetchNetworkError(FetchError, ProviderUnavailable):
    code = "fetch_network_error"
    message = "The website could not be reached."
