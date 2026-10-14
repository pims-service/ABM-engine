"""Public website fetching (issue #94): a safe, polite, dependency-light page fetcher.

Standard library only; no models, no migrations, no API, nothing registered at import time.

* ``PublicWebFetcher.fetch(url, purpose=...)`` returns a ``FetchResult`` or raises a typed
  ``FetchError`` (a ``ProviderError``);
* ``fetch_site_pages(url)`` fetches the homepage plus a few high-value same-site pages;
* settings live in ``settings.WEBSITE_FETCH`` (``config.DEFAULTS``).

Everything in a ``FetchResult`` that came from the page is untrusted data; wrap it with
``apps.integrations.llm.wrap_untrusted`` before it reaches a prompt. See
``docs/integrations/website-fetching.md``.
"""

from .errors import (
    POLICY_REASON,
    FetchBlockedByPolicy,
    FetchBlockedByRobots,
    FetchBlockedUnsafeTarget,
    FetchError,
    FetchHttpError,
    FetchInvalidUrl,
    FetchNetworkError,
    FetchOptedOut,
    FetchTimeout,
    FetchTooLarge,
    FetchUnsupportedContent,
)
from .extract import ExtractedPage, Link, OrganizationHint, extract_page
from .fetcher import FetchResult, PublicWebFetcher, RobotsDecision
from .politeness import HostRateLimiter
from .safety import ip_is_public, is_denied_host
from .site import SitePage, SitePages, SkippedPage, fetch_site_pages
from .transport import StdlibTransport, Transport, TransportError, TransportRequest

__all__ = [
    "POLICY_REASON",
    "ExtractedPage",
    "FetchBlockedByPolicy",
    "FetchBlockedByRobots",
    "FetchBlockedUnsafeTarget",
    "FetchError",
    "FetchHttpError",
    "FetchInvalidUrl",
    "FetchNetworkError",
    "FetchOptedOut",
    "FetchResult",
    "FetchTimeout",
    "FetchTooLarge",
    "FetchUnsupportedContent",
    "HostRateLimiter",
    "Link",
    "OrganizationHint",
    "PublicWebFetcher",
    "RobotsDecision",
    "SitePage",
    "SitePages",
    "SkippedPage",
    "StdlibTransport",
    "Transport",
    "TransportError",
    "TransportRequest",
    "extract_page",
    "fetch_site_pages",
    "ip_is_public",
    "is_denied_host",
]
