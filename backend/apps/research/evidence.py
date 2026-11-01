"""Where a fact came from: the one place that records evidence, and what it can say about it
(issue #100, Brief section 5: "store source and date for important evidence").

``record_evidence`` creates the ``DataSource`` that a research snapshot, a signal or a contact
points to. It is the entry point for research code; ``companies.services.create_data_source``
holds the same rules for every other caller, so nothing can store evidence without a source name
or with a date from the future. What the database enforces on top: a snapshot and a signal have
a non-null foreign key to their source, and a source that is not manual or provider needs a URL.

``company_evidence`` lists the evidence behind one company (for the API). ``age_days`` is the
freshness helper the trigger rules use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Client
from apps.companies.models import Company, CompanyResearch, DataSource
from apps.companies.services import create_data_source
from apps.research.models import Signal


def record_evidence(
    client: Client,
    type: str,
    name: str,
    *,
    url: str = "",
    provider_reference: str = "",
    observed_at: datetime | None = None,
    evidence_date: date | None = None,
    user: User | None = None,
) -> DataSource:
    """Record one retrieval of evidence and return its ``DataSource``.

    ``name`` is the provider, site or publication; ``observed_at`` is when we retrieved it
    (default now); ``evidence_date`` is the date the source itself states (publication or event
    date), when it states one. Raises ``ValidationError`` for an empty name, a retrieval or an
    event date in the future, or a missing URL where one is required.
    """
    return create_data_source(
        client,
        type,
        name,
        url=url,
        provider_reference=provider_reference,
        retrieved_at=observed_at,
        evidence_date=evidence_date,
        user=user,
    )


def age_days(moment: datetime | date, now: datetime | None = None) -> int:
    """Whole days between ``moment`` and ``now`` (default now); never negative."""
    current = now or timezone.now()
    if isinstance(moment, datetime):
        if timezone.is_naive(moment):
            moment = moment.replace(tzinfo=UTC)
        return max(0, (current - moment).days)
    return max(0, (current.date() - moment).days)


@dataclass(frozen=True)
class EvidenceRow:
    """One source and what it backs."""

    source: DataSource
    backs: list[dict[str, Any]]


def signal_sources(signal: Signal) -> list[DataSource]:
    """Every source of ``signal``: its own, then those of the signals it supersedes.

    When a second provider reports the same event, the collectors add a signal that supersedes
    the first, so the current signal appears once and both sources stay attached.
    """
    sources: list[DataSource] = []
    seen: set[Any] = set()
    node: Signal | None = signal
    while node is not None and node.pk not in seen:
        seen.add(node.pk)
        if node.data_source not in sources:
            sources.append(node.data_source)
        node = node.supersedes
    return sources


def company_evidence(company: Company, now: datetime | None = None) -> list[dict[str, Any]]:
    """The evidence behind ``company``: each source once, newest first, with what it backs.

    A source backs a research snapshot and/or a signal (a signal's earlier sources count: see
    ``signal_sources``). Each row has the source name, type, URL, when it was retrieved, the date
    the source states, and its age in days.
    """
    current = now or timezone.now()
    rows: dict[Any, EvidenceRow] = {}

    def add(source: DataSource, kind: str, ref: Any, detail: str) -> None:
        row = rows.setdefault(source.pk, EvidenceRow(source, []))
        row.backs.append({"kind": kind, "id": str(ref), "detail": detail})

    snapshots = CompanyResearch.objects.filter(company=company).select_related("data_source")
    for snapshot in snapshots:
        add(
            snapshot.data_source,
            "research_snapshot",
            snapshot.pk,
            snapshot.researched_at.date().isoformat(),
        )
    signals = Signal.objects.filter(company=company).select_related(
        "data_source", "supersedes__data_source"
    )
    for signal in signals:
        for source in signal_sources(signal):
            add(source, "signal", signal.pk, f"{signal.type}, {signal.event_date.isoformat()}")

    ordered = sorted(rows.values(), key=lambda r: r.source.retrieved_at, reverse=True)
    return [
        {
            "id": str(row.source.pk),
            "type": row.source.type,
            "name": row.source.name,
            "url": row.source.url,
            "provider_reference": row.source.provider_reference,
            "retrieved_at": row.source.retrieved_at,
            "evidence_date": row.source.evidence_date,
            "age_days": age_days(row.source.retrieved_at, current),
            "backs": row.backs,
        }
        for row in ordered
    ]
