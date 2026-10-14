"""Settings of the website fetcher (issue #94). All optional; no environment variables.

Override single keys in ``settings.WEBSITE_FETCH`` (a dict). Timeouts and the user agent are not
set here: they come from the resilience layer (``TimeoutPolicy.from_settings()`` and
``user_agent()``), so every outbound call of the product identifies itself the same way.

    WEBSITE_FETCH = {"min_delay": 2.0, "extra_denied_hosts": ["example-social.test"]}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from django.conf import settings

#: Hosts we never fetch, with all their subdomains (Brief §4: no unauthorized scraping of social
#: or professional networks). ``extra_denied_hosts`` in settings can only add to this list.
BASE_DENIED_HOSTS: Final[tuple[str, ...]] = (
    "linkedin.com",
    "lnkd.in",
    "facebook.com",
    "fb.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "t.co",
)

DEFAULTS: Final[Mapping[str, Any]] = {
    "max_bytes": 2 * 1024 * 1024,  # response body, after decompression
    "max_compressed_bytes": 2 * 1024 * 1024,  # bytes read from the wire
    "max_redirects": 5,
    "allowed_ports": (80, 443),
    "min_delay": 1.0,  # seconds between two requests to the same host
    "max_crawl_delay": 30.0,  # a larger Crawl-delay is capped to this (we never sleep longer)
    "robots_ttl": 3600.0,  # cache of a robots.txt answer
    "robots_failure_ttl": 300.0,  # cache of an unreachable robots.txt (treated as disallow)
    "robots_max_bytes": 512 * 1024,  # RFC 9309: parse at least 500 KiB
    "max_text_chars": 20_000,
    "max_links": 50,
    "max_jsonld_bytes": 20_000,
    "total_deadline": 60.0,  # seconds for one fetch (all hops, robots included)
    "site_total_bytes": 6 * 1024 * 1024,  # fetch_site_pages budget
    "site_total_seconds": 90.0,
    "extra_denied_hosts": (),
}


def get(name: str) -> Any:
    if name not in DEFAULTS:
        raise KeyError(f"Unknown website fetch setting {name!r}.")
    overrides: Mapping[str, Any] = getattr(settings, "WEBSITE_FETCH", None) or {}
    return overrides.get(name, DEFAULTS[name])


def denied_hosts() -> tuple[str, ...]:
    """The base denylist plus ``extra_denied_hosts`` (bad entries are ignored)."""
    extra = tuple(
        h.strip().lower().strip(".")
        for h in (get("extra_denied_hosts") or ())
        if isinstance(h, str) and h.strip().strip(".")
    )
    return BASE_DENIED_HOSTS + extra
