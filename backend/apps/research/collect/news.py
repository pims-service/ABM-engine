"""The news collector (issue #99, Brief section 7): recent news about a company as signals.

A news source is anything with ``fetch(company, since)`` that returns ``NewsItem`` rows. No real
news provider is built in: the source is chosen by ``settings.RESEARCH_NEWS_PROVIDER`` and the
only one that exists is ``fake`` (offline, only where ``PROVIDER_FAKE_ENABLED`` allows it). With
no source configured the step is skipped and says so; it never invents news. A real source is one
more class in ``NEWS_SOURCES`` (see docs/research-engine.md).

Each item is tagged with a signal type by keyword rules (funding, expansion, leadership, office,
product, partnership). An item with no clear type is not stored: only evidence we can name counts.
Items outside ``RESEARCH_NEWS_WINDOW_DAYS``, without a link or a date, or dated in the future are
left out and counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company, DataSource, DataSourceType
from apps.research.collect.signals import SignalOutcome, store_signal
from apps.research.evidence import record_evidence
from apps.research.models import SignalType


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    publisher: str
    published: date | None = None
    summary: str = ""


class NewsSource(Protocol):
    def fetch(self, company: Company, since: date) -> list[NewsItem]: ...


class NewsUnavailable(Exception):
    """No news source is configured. The message is safe to show."""


class FakeNewsSource:
    """Offline source for development and tests: items come from ``items``, or one fixed story
    for companies on ``example.com``."""

    def __init__(self, items: list[NewsItem] | None = None) -> None:
        self.items = items

    def fetch(self, company: Company, since: date) -> list[NewsItem]:
        if self.items is not None:
            return list(self.items)
        if not (company.domain or "").endswith("example.com"):
            return []
        return [
            NewsItem(
                title=f"{company.name} raises Series B to expand into Saudi Arabia",
                url=f"https://news.example.com/{company.domain}/series-b",
                publisher="Example News",
                published=timezone.now().date() - timedelta(days=20),
                summary="The round will fund a new regional sales team.",
            )
        ]


NEWS_SOURCES: dict[str, type[Any]] = {"fake": FakeNewsSource}

#: Keyword rules, first match wins. Lower-case; matched against the title and summary.
_RULES: tuple[tuple[str, str], ...] = (
    (SignalType.FUNDING, r"\b(raises?|raised|funding|series [a-e]|seed round)\b"),
    (
        SignalType.NEW_LEADERSHIP,
        r"\b(appoints?|appointed|names?|new (ceo|cto|cfo|cmo|coo|chief|head|vp))\b",
    ),
    (
        SignalType.NEW_OFFICE,
        r"\b(opens? (a |an |its )?(new )?(office|hub|branch|headquarters)|new office)\b",
    ),
    (SignalType.MARKET_EXPANSION, r"\b(expands?|expansion|enters?|launches? in)\b"),
    (
        SignalType.NEW_PRODUCT,
        r"\b(launch(es|ed)?|unveils?|introduces?|releases?) .*"
        r"\b(product|platform|app|service|solution)\b",
    ),
    (
        SignalType.MAJOR_PARTNERSHIP,
        r"\b(partners? with|partnership|teams? up with|strategic alliance)\b",
    ),
)


def tag_item(item: NewsItem) -> str | None:
    """The signal type of a news item, or ``None`` when no rule applies."""
    text = f"{item.title} {item.summary}".lower()
    for signal_type, pattern in _RULES:
        if re.search(pattern, text):
            return signal_type
    return None


def news_source() -> Any:
    """The configured news source, or ``NewsUnavailable``."""
    name = getattr(settings, "RESEARCH_NEWS_PROVIDER", "")
    if not name:
        raise NewsUnavailable("No news source is configured, so news was skipped.")
    if name not in NEWS_SOURCES:
        raise NewsUnavailable(f"The news source '{name}' is not supported.")
    if name == "fake" and not getattr(settings, "PROVIDER_FAKE_ENABLED", False):
        raise NewsUnavailable("The fake news source is only available in development.")
    return NEWS_SOURCES[name]()


@dataclass
class NewsOutcome(SignalOutcome):
    items_seen: int = 0
    untagged: int = 0
    out_of_window: int = 0
    unusable: int = 0
    skipped_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            **super().as_dict(),
            "items_seen": self.items_seen,
            "untagged": self.untagged,
            "out_of_window": self.out_of_window,
            "unusable": self.unusable,
            "skipped_reason": self.skipped_reason,
        }


def collect_news(
    company: Company, source: Any | None = None, *, user: User | None = None
) -> NewsOutcome:
    """Collect news signals for ``company``; ``source`` defaults to the configured one."""
    outcome = NewsOutcome()
    try:
        feed = source if source is not None else news_source()
    except NewsUnavailable as exc:
        outcome.skipped_reason = str(exc)
        return outcome
    today = timezone.now().date()
    window = int(getattr(settings, "RESEARCH_NEWS_WINDOW_DAYS", 365))
    since = today - timedelta(days=window)
    items = feed.fetch(company, since)
    outcome.items_seen = len(items)
    for item in items:
        if not item.url or not item.publisher.strip():
            outcome.unusable += 1
            continue
        if item.published is None:
            outcome.undated += 1
            continue
        if item.published < since:
            outcome.out_of_window += 1
            continue
        signal_type = tag_item(item)
        if signal_type is None:
            outcome.untagged += 1
            continue
        evidence = item.title if not item.summary else f"{item.title}. {item.summary}"
        outcome.add(
            store_signal(
                company,
                (DataSourceType.NEWS, item.publisher.strip()),
                _maker(company, item, user),
                signal_type,
                evidence,
                item.published,
                user=user,
            )
        )
    return outcome


def _maker(company: Company, item: NewsItem, user: User | None) -> Any:
    def make() -> DataSource:
        return record_evidence(
            company.client,
            DataSourceType.NEWS,
            item.publisher.strip(),
            url=item.url,
            evidence_date=item.published,
            user=user,
        )

    return make
