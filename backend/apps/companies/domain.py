"""Company domain normalization (data model: "Domain normalization").

One function, used on every write (``Company.save`` calls it), so the unique
``(campaign, domain)`` rule compares like with like.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
_MAX_DOMAIN_LENGTH = 253


def normalize_domain(website: str | None) -> str | None:
    """Return the normalized host of ``website``, or ``None`` when there is no usable domain.

    Rules: take the host (a bare ``example.com/path`` works without a scheme), lowercase it,
    drop scheme, credentials, port, path, query, a leading ``www.`` and trailing dots, and
    convert internationalized names to punycode. Other subdomains are kept (``eu.example.com``
    stays as is: stripping to the registrable domain needs a public suffix list, see
    decision 11 in docs/data-model.md). IP addresses, single labels such as ``localhost`` and
    malformed input give ``None``, which means "no domain" (the company is then matched by
    a same-name warning).
    """
    text = (website or "").strip()
    if not text:
        return None
    if not _SCHEME.match(text):
        text = "//" + text.removeprefix("//")
    try:
        host = urlsplit(text).hostname  # lowercases, drops userinfo and port
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    host = host.lower().removeprefix("www.")
    labels = host.split(".")
    if (
        len(host) > _MAX_DOMAIN_LENGTH
        or len(labels) < 2
        or not all(_LABEL.match(label) for label in labels)
        or labels[-1].isdigit()  # an IPv4 address, not a domain
    ):
        return None
    return host
