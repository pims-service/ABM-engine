"""Tunable parameters of the growth calculation. Nothing here is hard-wired policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: Windows reported for every company and department.
WINDOWS: tuple[int, ...] = (3, 6, 12)

#: Default tolerance (days either side of ``as_of - months``) for finding the baseline point.
DEFAULT_TOLERANCE_DAYS: Mapping[int, int] = MappingProxyType({3: 21, 6: 30, 12: 45})

#: Source trust order: a ``source`` string starting with an earlier prefix wins a same-day
#: conflict. Anything matching no prefix ranks last.
DEFAULT_SOURCE_PRIORITY: tuple[str, ...] = ("manual:", "filing:", "company:", "provider:")


@dataclass(frozen=True)
class GrowthConfig:
    """Parameters for series building and growth computation.

    ``tolerance_days``: per window, how far from ``as_of - months`` a real point may sit and
    still be used as the baseline (windows not listed use ``fallback_tolerance_days``).
    ``max_gap_days``: two points further apart than this are never interpolated.
    ``latest_max_age_days``: the latest point must be at most this old at ``as_of``.
    ``low_base_threshold``: a baseline below this many employees is flagged ``low_base``.
    ``max_headcount``: larger counts are rejected as implausible when building a series.
    ``commercial_min_components``: departments needed for the commercial-team roll-up.
    ``commercial_align_days``: how far apart the component points of one summed point may be.
    ``source_priority``: source-prefix trust order for same-day conflicts.
    """

    tolerance_days: Mapping[int, int] = field(default=DEFAULT_TOLERANCE_DAYS)
    fallback_tolerance_days: int = 30
    max_gap_days: int = 120
    latest_max_age_days: int = 45
    low_base_threshold: int = 10
    max_headcount: int = 5_000_000
    commercial_min_components: int = 2
    commercial_align_days: int = 14
    source_priority: tuple[str, ...] = DEFAULT_SOURCE_PRIORITY

    def tolerance_for(self, months: int) -> int:
        return self.tolerance_days.get(months, self.fallback_tolerance_days)


DEFAULT_CONFIG = GrowthConfig()
