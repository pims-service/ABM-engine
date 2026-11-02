"""How long a signal counts as current (Brief section 7: no stale event stays "current"), and the
windows a campaign may set for itself (issue #114).

Kept outside ``apps.research.engine`` on purpose: that package imports the pipeline, which imports
``services``, which needs these helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from django.core.exceptions import ValidationError

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


#: The longest window a campaign may set (ten years): a typo of 36500 is refused, not stored.
MAX_WINDOW_DAYS = 3650


def validate_windows(raw: Any) -> dict[str, int]:
    """A campaign's windows from user input: ``{signal type: days}`` with whole days from 1 to
    ``MAX_WINDOW_DAYS``. An empty object means "use the defaults". Raises ``ValidationError``
    naming the bad types (never silently drops anything)."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationError("Trigger windows must be an object of signal type to days.")
    errors: dict[str, str] = {}
    clean: dict[str, int] = {}
    for key, days in raw.items():
        if key not in SignalType.values:
            errors[str(key)] = "Unknown signal type."
        elif isinstance(days, bool) or not isinstance(days, int):
            errors[str(key)] = "Days must be a whole number."
        elif not 1 <= days <= MAX_WINDOW_DAYS:
            errors[str(key)] = f"Days must be from 1 to {MAX_WINDOW_DAYS}."
        else:
            clean[str(key)] = days
    if errors:
        raise ValidationError(errors)
    return clean


def usable_windows(stored: Any) -> dict[str, int]:
    """The windows to apply from what a campaign stored. Entries that are not valid (an old
    signal type, a bad number) are ignored here, so a bad row can never break a dashboard."""
    if not isinstance(stored, Mapping):
        return {}
    return {
        str(key): days
        for key, days in stored.items()
        if key in SignalType.values
        and isinstance(days, int)
        and not isinstance(days, bool)
        and 1 <= days <= MAX_WINDOW_DAYS
    }


def window_days(signal_type: str, windows: Mapping[str, int] | None = None) -> int:
    """How many days a signal of ``signal_type`` stays fresh: the campaign's window when it set
    one, else the default."""
    if windows and signal_type in windows:
        return windows[signal_type]
    return FRESH_DAYS.get(signal_type, FRESH_DAYS[SignalType.OTHER])


def expiry_for(
    signal_type: str, event_date: date, windows: Mapping[str, int] | None = None
) -> datetime:
    """When a signal of ``signal_type`` that happened on ``event_date`` stops being fresh."""
    days = window_days(signal_type, windows)
    start = datetime(event_date.year, event_date.month, event_date.day, tzinfo=UTC)
    return start + timedelta(days=days)
