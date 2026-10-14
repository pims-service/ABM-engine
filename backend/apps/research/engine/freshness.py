"""How long a signal counts as current (Brief section 7: no stale event stays "current")."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from apps.research.models import SignalType

#: Days after the event during which a signal counts as fresh.
FRESH_DAYS: dict[str, int] = {
    SignalType.SALES_HIRING: 60,
    SignalType.BD_HIRING: 60,
    SignalType.COMMERCIAL_HIRING: 60,
    SignalType.PARTNERSHIPS_HIRING: 60,
    SignalType.HEADCOUNT_GROWTH: 120,
    SignalType.COMMERCIAL_TEAM_GROWTH: 120,
    SignalType.FUNDING: 365,
    SignalType.MARKET_EXPANSION: 180,
    SignalType.NEW_LEADERSHIP: 180,
    SignalType.NEW_OFFICE: 180,
    SignalType.NEW_PRODUCT: 180,
    SignalType.MAJOR_PARTNERSHIP: 180,
    SignalType.OTHER: 90,
}


def expiry_for(signal_type: str, event_date: date) -> datetime:
    """When a signal of ``signal_type`` that happened on ``event_date`` stops being fresh."""
    days = FRESH_DAYS.get(signal_type, FRESH_DAYS[SignalType.OTHER])
    start = datetime(event_date.year, event_date.month, event_date.day, tzinfo=UTC)
    return start + timedelta(days=days)
