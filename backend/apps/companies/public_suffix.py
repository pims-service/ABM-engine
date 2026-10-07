"""A small embedded list of multi-part public suffixes (issue #57).

Why not ``tldextract``: it ships (or downloads) the full Public Suffix List, needs a cache
directory or a network call on first use, and adds a dependency for one question: "where does
the registrable part of this host start?". The companies we research sit in a handful of
markets, so a short, reviewed list of the multi-part suffixes that actually occur there
(``co.uk``, ``com.sa``, ``com.au``, ``co.in``, ...) gets the right answer for them without
network access.

Tradeoff: a suffix missing from the list makes ``registrable_domain("shop.example.co.xx")``
return ``co.xx`` instead of ``example.co.xx``. That error is safe, because the function is
advisory (``normalize_domain`` and the stored ``Company.domain`` never use it), and fixing it
means adding one line here and bumping ``SUFFIX_LIST_VERSION``. Single-label TLDs need no entry:
the registrable domain of anything under one is the last two labels.
"""

from __future__ import annotations

SUFFIX_LIST_VERSION = "2026.10.1"

MULTI_PART_SUFFIXES: frozenset[str] = frozenset(
    {
        # United Kingdom
        "ac.uk",
        "co.uk",
        "gov.uk",
        "ltd.uk",
        "me.uk",
        "net.uk",
        "org.uk",
        "plc.uk",
        "sch.uk",
        # Saudi Arabia
        "com.sa",
        "edu.sa",
        "gov.sa",
        "med.sa",
        "net.sa",
        "org.sa",
        "sch.sa",
        # United Arab Emirates
        "ac.ae",
        "co.ae",
        "gov.ae",
        "net.ae",
        "org.ae",
        "sch.ae",
        # Other Gulf and Middle East
        "com.bh",
        "com.eg",
        "com.jo",
        "com.kw",
        "com.lb",
        "com.om",
        "com.qa",
        "co.il",
        "com.tr",
        # Indian subcontinent
        "ac.in",
        "co.in",
        "edu.in",
        "firm.in",
        "gen.in",
        "gov.in",
        "ind.in",
        "net.in",
        "org.in",
        "res.in",
        "com.bd",
        "com.pk",
        "com.lk",
        # Oceania
        "com.au",
        "edu.au",
        "gov.au",
        "net.au",
        "org.au",
        "co.nz",
        "net.nz",
        "org.nz",
        # Asia
        "com.cn",
        "com.hk",
        "com.my",
        "com.ph",
        "com.sg",
        "com.tw",
        "com.vn",
        "co.id",
        "co.jp",
        "ne.jp",
        "or.jp",
        "co.kr",
        "co.th",
        # Africa
        "co.ke",
        "co.za",
        "com.ng",
        # Americas and Europe
        "com.ar",
        "com.br",
        "com.co",
        "com.mx",
        "com.pe",
        "com.ua",
    }
)


def registrable_domain(host: str | None) -> str | None:
    """Return the registrable domain of an already-normalized ``host``, or ``None``.

    ``eu.shop.example.co.uk`` -> ``example.co.uk``; ``www2.example.com`` -> ``example.com``.
    ``None`` when ``host`` is blank, has a single label, or is itself a public suffix
    (``co.uk``). ``host`` should come from ``normalize_domain`` (lower case, punycode).
    """
    if not host:
        return None
    labels = host.lower().strip(".").split(".")
    if len(labels) < 2 or not all(labels):
        return None
    suffix_len = 2 if ".".join(labels[-2:]) in MULTI_PART_SUFFIXES else 1
    if len(labels) <= suffix_len:
        return None
    return ".".join(labels[-(suffix_len + 1) :])
