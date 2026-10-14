"""Result types of the growth calculation."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .series import HeadcountPoint, RejectedPoint

COMPANY = "company"
COMMERCIAL_TEAM = "commercial_team"


class GrowthStatus(StrEnum):
    #: Computed, nothing to flag.
    OK = "ok"
    #: Computed, but the baseline is below ``low_base_threshold`` employees: a few hires look
    #: like large growth. Always read together with ``absolute_change``.
    LOW_BASE = "low_base"
    #: Unknown. No usable baseline or latest point; ``percent`` is ``None``, never 0.
    INSUFFICIENT_DATA = "insufficient_data"
    #: The baseline headcount is zero, so a percentage does not exist.
    UNDEFINED_BASELINE = "undefined_baseline"
    #: The percentage does not fit ``Decimal(6, 2)`` (beyond +-9999.99); not clamped.
    OUT_OF_RANGE = "out_of_range"


class GrowthMethod(StrEnum):
    #: Both end points are real observations (the baseline is the nearest within tolerance).
    OBSERVED = "observed"
    #: The baseline was linearly interpolated between the two points around the window start.
    INTERPOLATED = "interpolated"
    #: The percentage came from the provider; no dates or counts are known.
    PROVIDER = "provider"


@dataclass(frozen=True)
class ResolvedPoint:
    """An end point of a growth window. ``count`` is a Decimal because an interpolated
    baseline need not be whole; ``basis`` holds the two real points it was interpolated from."""

    date: dt.date
    count: Decimal
    source: str
    interpolated: bool = False
    basis: tuple[HeadcountPoint, ...] = ()


@dataclass(frozen=True)
class GrowthResult:
    """One growth figure. ``percent`` ``None`` means unknown, and ``reason`` says why."""

    scope: str
    months_requested: int
    status: GrowthStatus
    percent: Decimal | None = None
    absolute_change: Decimal | None = None
    from_point: ResolvedPoint | None = None
    to_point: ResolvedPoint | None = None
    months_actual: Decimal | None = None
    method: GrowthMethod | None = None
    reason: str = ""
    components: tuple[str, ...] = ()
    provider_source: str | None = None

    @property
    def is_available(self) -> bool:
        """True when ``percent`` is a real figure (``ok`` or ``low_base``)."""
        return self.status in (GrowthStatus.OK, GrowthStatus.LOW_BASE)


@dataclass(frozen=True)
class GrowthWindows:
    """The 3, 6 and 12 month results of one scope."""

    m3: GrowthResult
    m6: GrowthResult
    m12: GrowthResult

    def get(self, months: int) -> GrowthResult:
        return {3: self.m3, 6: self.m6, 12: self.m12}[months]

    def results(self) -> tuple[GrowthResult, GrowthResult, GrowthResult]:
        return (self.m3, self.m6, self.m12)


@dataclass(frozen=True)
class GrowthReport:
    """Growth for the company, each department and the derived commercial-team roll-up.

    ``departments`` keys are canonical department names, in canonical order. ``rejected``
    lists every input point that was discarded, and ``ignored_departments`` the department
    keys that were not recognised.
    """

    as_of: dt.date
    company: GrowthWindows
    departments: Mapping[str, GrowthWindows]
    commercial_team: GrowthWindows
    rejected: tuple[RejectedPoint, ...] = ()
    ignored_departments: tuple[str, ...] = ()

    def all_results(self) -> tuple[GrowthResult, ...]:
        """Company, departments (canonical order) then commercial team; 3, 6, 12 within each."""
        out: list[GrowthResult] = list(self.company.results())
        for windows in self.departments.values():
            out.extend(windows.results())
        out.extend(self.commercial_team.results())
        return tuple(out)
