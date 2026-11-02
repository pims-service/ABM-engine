"""How long a signal counts as current: see ``apps.research.windows`` (re-exported here)."""

from __future__ import annotations

from apps.research.windows import (
    FRESH_DAYS,
    MAX_WINDOW_DAYS,
    expiry_for,
    usable_windows,
    validate_windows,
    window_days,
)

__all__ = [
    "FRESH_DAYS",
    "MAX_WINDOW_DAYS",
    "expiry_for",
    "usable_windows",
    "validate_windows",
    "window_days",
]
