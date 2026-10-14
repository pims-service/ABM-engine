"""``fetch_site_pages``: the homepage plus a few high-value pages of the same site (issue #94).

Pages are discovered from the homepage links with simple keyword rules over the URL path and the
anchor text, in this priority: about, products or services, careers, news or press, contact. At
most one page per kind is taken first, then the remaining slots are filled with the next best
links. The whole run has a byte budget and a time budget (``site_total_bytes``,
``site_total_seconds``); a page that fails (robots, timeout, 404, opt-out) is recorded in
``skipped`` and the run goes on. Only a failure on the homepage itself raises, because without it
nothing is known about the site.

Every page carries its provenance (source URL, retrieved_at, content hash) so evidence can cite
it. Nothing is persisted here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import config
from .errors import FetchError
from .extract import Link, site_key
from .fetcher import FetchResult, PublicWebFetcher

#: Kind, then the keywords looked for in path segments and anchor text, in priority order.
PAGE_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("about", ("about", "about-us", "company", "who-we-are", "our-story", "our-company")),
    (
        "products",
        ("products", "product", "solutions", "solution", "services", "platform", "what-we-do"),
    ),
    ("careers", ("careers", "career", "jobs", "join-us", "work-with-us", "hiring")),
    ("news", ("news", "newsroom", "press", "media", "press-releases", "announcements")),
    ("contact", ("contact", "contact-us", "get-in-touch")),
)
MAX_PATH_DEPTH = 3
_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SitePage:
    kind: str  # "home", "about", "products", "careers", "news" or "contact"
    result: FetchResult
    provenance: dict[str, str]  # source_url, retrieved_at, content_hash, robots, purpose


@dataclass(frozen=True)
class SkippedPage:
    url: str
    kind: str
    code: str  # a FetchError code, or "budget"


@dataclass(frozen=True)
class SitePages:
    pages: tuple[SitePage, ...]
    skipped: tuple[SkippedPage, ...]
    bytes_read: int
    elapsed_seconds: float
    budget_exhausted: bool


def _score(link: Link, keywords: tuple[str, ...]) -> int | None:
    """Lower is better; ``None`` when the link does not look like this kind of page."""
    parts = urlsplit(link.url)
    segments = [s for s in parts.path.lower().split("/") if s]
    if len(segments) > MAX_PATH_DEPTH or parts.query:
        return None
    slug_tokens = {"-".join(_SPLIT.split(s)).strip("-") for s in segments}
    text = "-".join(t for t in _SPLIT.split(link.text.lower()) if t)
    best: int | None = None
    for rank, keyword in enumerate(keywords):
        if keyword in slug_tokens:
            candidate = rank + 10 * (len(segments) - 1)
        elif segments and keyword == text:
            candidate = rank + 5 + 10 * (len(segments) - 1)
        else:
            continue
        best = candidate if best is None else min(best, candidate)
    return best


def discover_pages(home: FetchResult, limit: int) -> list[tuple[str, Link]]:
    """Up to ``limit`` ``(kind, link)`` pairs from the homepage links, best first."""
    seen = {_dedupe_key(home.final_url), _dedupe_key(home.url)}
    ranked: dict[str, list[tuple[int, Link]]] = {}
    for link in home.links:
        key = _dedupe_key(link.url)
        if key in seen:
            continue
        for kind, keywords in PAGE_KINDS:
            score = _score(link, keywords)
            if score is not None:
                ranked.setdefault(kind, []).append((score, link))
    for options in ranked.values():
        options.sort(key=lambda item: item[0])
    chosen: list[tuple[str, Link]] = []
    used: set[str] = set()

    def take(kind: str, link: Link) -> None:
        key = _dedupe_key(link.url)
        if key not in used and len(chosen) < limit:
            used.add(key)
            chosen.append((kind, link))

    for kind, _ in PAGE_KINDS:  # one per kind first
        for _, link in ranked.get(kind, [])[:1]:
            take(kind, link)
    for kind, _ in PAGE_KINDS:  # then the runners up
        for _, link in ranked.get(kind, [])[1:]:
            take(kind, link)
    return chosen


def _dedupe_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{site_key(parts.hostname or '')}{parts.path.rstrip('/')}"


def fetch_site_pages(
    url: str,
    *,
    max_pages: int = 5,
    purpose: str = "company_site",
    fetcher: PublicWebFetcher | None = None,
) -> SitePages:
    """Fetch the homepage and up to ``max_pages - 1`` same-site pages (``max_pages`` is clamped to
    1..10). Raises the homepage's ``FetchError``; other failures end up in ``skipped``."""
    fetcher = fetcher or PublicWebFetcher()
    max_pages = max(1, min(int(max_pages), 10))
    clock = fetcher.clock
    started = clock()
    byte_budget = int(config.get("site_total_bytes"))
    time_budget = float(config.get("site_total_seconds"))

    home = fetcher.fetch(url, purpose=purpose)
    pages = [SitePage("home", home, home.provenance())]
    skipped: list[SkippedPage] = []
    total_bytes = home.bytes_read
    exhausted = False
    for kind, link in discover_pages(home, max_pages - 1):
        if total_bytes >= byte_budget or clock() - started >= time_budget:
            skipped.append(SkippedPage(link.url, kind, "budget"))
            exhausted = True
            continue
        try:
            result = fetcher.fetch(link.url, purpose=purpose)
        except FetchError as exc:
            skipped.append(SkippedPage(link.url, kind, exc.code))
            continue
        total_bytes += result.bytes_read
        pages.append(SitePage(kind, result, result.provenance()))
    return SitePages(
        pages=tuple(pages),
        skipped=tuple(skipped),
        bytes_read=total_bytes,
        elapsed_seconds=clock() - started,
        budget_exhausted=exhausted,
    )
