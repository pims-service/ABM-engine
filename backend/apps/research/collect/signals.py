"""The signal collectors (issue #98, Brief section 7): funding, hiring and provider signals.

Three kinds of evidence become ``Signal`` rows here:

* what the provider reports as signals, and its last funding round;
* open roles from the provider's job postings, grouped by the department the title belongs to
  (``apps.research.taxonomy``): one signal per department, such as "3 open sales roles";
* the news collector uses ``store_signal`` too (``apps.research.collect.news``).

``store_signal`` is the one place a collector writes a signal. It never invents a date: a
candidate without a date cannot be stored (a ``Signal`` needs the date of the event) and is only
counted, so the run report says how many were left out. When a second source reports an event we
already hold (same type, same day) the existing signal is superseded by a new row that adds the
source, so the event appears once and both sources stay on record. The same source reporting it
again changes nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company, DataSource, DataSourceType
from apps.integrations.framework import NormalizedJobPosting, Provenance
from apps.integrations.framework.base import supports
from apps.integrations.framework.categories import JOB_POSTINGS
from apps.research.collect.profile import ProfileOutcome, company_ref
from apps.research.engine.freshness import expiry_for
from apps.research.evidence import record_evidence, signal_sources
from apps.research.growth import (
    COMMERCIAL_TEAM_GROWTH,
    HEADCOUNT_GROWTH,
    GrowthTriggerHint,
)
from apps.research.models import Signal, SignalType
from apps.research.services import create_signal, supersede_signal
from apps.research.taxonomy import classify_title

CREATED = "created"
CORROBORATED = "corroborated"
DUPLICATE = "duplicate"
UNDATED = "undated"
FUTURE = "future"

#: The hiring signal of each department; marketing hiring is not a Brief section 7 trigger.
HIRING = {
    "sales": SignalType.SALES_HIRING,
    "bd": SignalType.BD_HIRING,
    "commercial": SignalType.COMMERCIAL_HIRING,
    "partnerships": SignalType.PARTNERSHIPS_HIRING,
}
_ROLE_WORD = {
    "sales": "sales",
    "bd": "business development",
    "commercial": "commercial",
    "partnerships": "partnerships",
}


@dataclass
class SignalOutcome:
    """Counts of what a signal step did, for the run report."""

    created: int = 0
    corroborated: int = 0
    duplicates: int = 0
    undated: int = 0
    future: int = 0
    postings_seen: int = 0
    postings_unclassified: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, verdict: str) -> None:
        match verdict:
            case "created":
                self.created += 1
            case "corroborated":
                self.corroborated += 1
            case "duplicate":
                self.duplicates += 1
            case "undated":
                self.undated += 1
            case "future":
                self.future += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "corroborated": self.corroborated,
            "duplicates": self.duplicates,
            "undated": self.undated,
            "future": self.future,
            "postings_seen": self.postings_seen,
            "postings_unclassified": self.postings_unclassified,
            "notes": self.notes,
        }


def store_signal(
    company: Company,
    source_key: tuple[str, str],
    make_source: Callable[[], DataSource],
    signal_type: str,
    evidence: str,
    event_date: date | None,
    *,
    user: User | None = None,
) -> str:
    """Store one signal, or say why not: ``created``, ``corroborated``, ``duplicate``,
    ``undated`` or ``future``.

    ``source_key`` is ``(data source type, name)`` and decides whether a signal we hold came from
    the same source; ``make_source`` creates the evidence row, and is called only when something
    is stored, so a repeated run leaves no stray source behind.
    """
    if event_date is None:
        return UNDATED
    if event_date > timezone.now().date() + timedelta(days=1):
        return FUTURE
    existing = (
        Signal.objects.current()
        .filter(company=company, type=signal_type, event_date=event_date)
        .select_related("data_source", "supersedes__data_source")
        .first()
    )
    expires = expiry_for(signal_type, event_date)
    if existing is None:
        create_signal(
            company,
            make_source(),
            signal_type,
            evidence,
            event_date,
            expires_at=expires,
            user=user,
        )
        return CREATED
    if any((s.type, s.name) == source_key for s in signal_sources(existing)):
        return DUPLICATE
    supersede_signal(existing, make_source(), evidence, event_date, expires_at=expires, user=user)
    return CORROBORATED


def provider_source_factory(
    company: Company,
    provenance: Provenance,
    user: User | None,
    existing: DataSource | None = None,
) -> tuple[tuple[str, str], Callable[[], DataSource]]:
    """The key and the lazy, shared evidence row of one provider answer. ``existing`` is a row
    already made for this same answer (the profile step's), so it is not recorded twice."""
    made: list[DataSource] = [existing] if existing is not None else []

    def make() -> DataSource:
        if not made:
            made.append(
                record_evidence(
                    company.client,
                    DataSourceType.PROVIDER,
                    provenance.provider,
                    url=provenance.source_url or "",
                    provider_reference=provenance.source_ref or provenance.request_id or "",
                    observed_at=provenance.retrieved_at,
                    evidence_date=provenance.evidence_date,
                    user=user,
                )
            )
        return made[0]

    return (DataSourceType.PROVIDER, provenance.provider), make


def _money(amount: Any, currency: str | None) -> str:
    return f" of {currency or ''} {amount:,.0f}".replace("  ", " ") if amount else ""


def collect_signals(
    company: Company,
    adapter: Any,
    profile: ProfileOutcome | None = None,
    *,
    user: User | None = None,
) -> SignalOutcome:
    """Collect provider signals, funding and hiring for ``company`` (see the module docstring).

    ``profile`` is the profile step's outcome when it ran: its provider signals and funding are
    stored here, and the job postings are asked for either way.
    """
    outcome = SignalOutcome()
    data = profile.company_data if profile else None
    provenance = profile.provenance if profile else None
    if data is not None and provenance is not None:
        reuse = profile.source if profile and profile.status == "created" else None
        key, make = provider_source_factory(company, provenance, user, reuse)
        for candidate in data.signals:
            outcome.add(
                store_signal(
                    company,
                    key,
                    make,
                    candidate.type,
                    candidate.evidence,
                    candidate.event_date,
                    user=user,
                )
            )
        funding = data.funding
        if funding is not None and funding.last_round_date is not None:
            label = funding.last_round_type or "Funding round"
            amount = _money(funding.last_round_amount, funding.currency)
            outcome.add(
                store_signal(
                    company,
                    key,
                    make,
                    SignalType.FUNDING,
                    f"{label}{amount}, reported by {provenance.provider}.",
                    funding.last_round_date,
                    user=user,
                )
            )
        if profile is not None and profile.growth is not None:
            _growth_signals(company, profile, key, make, outcome, user)
    if supports(adapter, JOB_POSTINGS):
        _hiring(company, adapter, outcome, user)
    else:
        outcome.notes.append("The provider does not list job postings.")
    return outcome


def _growth_signals(
    company: Company,
    profile: ProfileOutcome,
    key: tuple[str, str],
    make: Callable[[], DataSource],
    outcome: SignalOutcome,
    user: User | None,
) -> None:
    """Headcount and commercial-team growth signals, when the measured growth meets the
    thresholds of ``GrowthTriggerHint``. Growth that is unknown, or too small, stores nothing.

    The event date is the day the headcount was observed. The evidence sentence carries the
    numbers, so the same measurement seen again is a duplicate and changed numbers are a new
    signal.
    """
    report = profile.growth
    if report is None:
        return
    hint = GrowthTriggerHint()
    for signal_type in (HEADCOUNT_GROWTH, COMMERCIAL_TEAM_GROWTH):
        result = hint.evaluate(report, signal_type)
        if not result.met:
            continue
        same = Signal.objects.current().filter(
            company=company, type=signal_type, evidence=result.evidence
        )
        if same.exists():
            outcome.add(DUPLICATE)
            continue
        outcome.add(
            store_signal(company, key, make, signal_type, result.evidence, report.as_of, user=user)
        )


def _hiring(company: Company, adapter: Any, outcome: SignalOutcome, user: User | None) -> None:
    result = adapter.list_job_postings(company_ref(company))
    postings: tuple[NormalizedJobPosting, ...] = result.data or ()
    outcome.postings_seen = len(postings)
    by_type: dict[str, list[NormalizedJobPosting]] = defaultdict(list)
    for posting in postings:
        title = classify_title(posting.title)
        if title.counts and title.department in HIRING:
            by_type[HIRING[title.department]].append(posting)
        else:
            outcome.postings_unclassified += 1
    key, make = provider_source_factory(company, result.provenance, user)
    for signal_type, group in by_type.items():
        dated = [p for p in group if p.posted_date is not None]
        undated = len(group) - len(dated)
        if undated:
            outcome.undated += undated
        if not dated:
            continue
        latest = max(p.posted_date for p in dated if p.posted_date)
        department = next(d for d, t in HIRING.items() if t == signal_type)
        titles = ", ".join(sorted({p.title for p in dated})[:5])
        count = len(dated)
        role = "role" if count == 1 else "roles"
        evidence = f"{count} open {_ROLE_WORD[department]} {role}: {titles}."
        outcome.add(store_signal(company, key, make, signal_type, evidence, latest, user=user))
