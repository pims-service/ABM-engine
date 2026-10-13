"""Input types and the series builder.

A ``HeadcountPoint`` is one observed headcount with its provenance. ``build_series`` turns
loose points into a clean ``HeadcountSeries``: sorted, one point per day, implausible or future
points removed. Nothing is dropped silently: every removed point is kept in
``HeadcountSeries.rejected`` with the reason.

Same-day conflicts: the point whose ``source`` starts with the earliest prefix of
``GrowthConfig.source_priority`` wins (default order ``manual:``, ``filing:``, ``company:``,
``provider:``, then anything else); a tie on priority goes to the alphabetically first source,
and a tie on that goes to the first point in the input.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from .config import DEFAULT_CONFIG, GrowthConfig
from .departments import normalize_department


@dataclass(frozen=True)
class HeadcountPoint:
    """One observed headcount.

    ``source`` is a provenance reference (a data source id, URL or ``provider:<name>:<ref>``
    string); a point without one is rejected. ``department`` is ``None`` for the company as a
    whole, otherwise a department name (normalised by ``normalize_department``).
    """

    date: dt.date
    count: int
    source: str
    department: str | None = None


@dataclass(frozen=True)
class RejectedPoint:
    point: HeadcountPoint
    reason: str


@dataclass(frozen=True)
class HeadcountSeries:
    """Clean, ascending, one-point-per-day series for the company (``department=None``) or a
    canonical department."""

    department: str | None
    points: tuple[HeadcountPoint, ...] = ()
    rejected: tuple[RejectedPoint, ...] = ()


@dataclass(frozen=True)
class GroupedSeries:
    """Result of ``group_points``: one series per canonical department (``None`` = company)."""

    series: Mapping[str | None, HeadcountSeries]
    rejected: tuple[RejectedPoint, ...] = ()


def _rank(source: str, config: GrowthConfig) -> int:
    for index, prefix in enumerate(config.source_priority):
        if source.startswith(prefix):
            return index
    return len(config.source_priority)


def _invalid_reason(point: HeadcountPoint, as_of: dt.date, config: GrowthConfig) -> str | None:
    # Callers are untyped at runtime (collectors, JSON), so check the values as ``object``.
    raw_date: object = point.date
    raw_count: object = point.count
    raw_source: object = point.source
    if not isinstance(raw_date, dt.date):
        return "date is not a date"
    day = raw_date.date() if isinstance(raw_date, dt.datetime) else raw_date
    if not isinstance(raw_count, int) or isinstance(raw_count, bool):
        return "headcount must be an integer"
    if not isinstance(raw_source, str) or not raw_source.strip():
        return "headcount has no source (provenance is required)"
    if point.count < 0:
        return f"negative headcount ({point.count})"
    if point.count > config.max_headcount:
        return f"implausible headcount ({point.count}, maximum {config.max_headcount})"
    if day > as_of:
        return f"date {day.isoformat()} is after as_of {as_of.isoformat()}"
    return None


def build_series(
    points: Iterable[HeadcountPoint],
    *,
    as_of: dt.date,
    department: str | None = None,
    config: GrowthConfig = DEFAULT_CONFIG,
) -> HeadcountSeries:
    """Build a clean series of one scope (the company, or one canonical department).

    Every point must belong to ``department`` (``None`` means company-level, i.e. point
    department ``None``); a mismatch is a caller bug and raises ``ValueError``. Use
    ``group_points`` to split mixed input first.
    """
    rejected: list[RejectedPoint] = []
    by_day: dict[dt.date, list[HeadcountPoint]] = {}
    for original in points:
        owner = None if original.department is None else normalize_department(original.department)
        if owner != department:
            raise ValueError(
                f"point for department {original.department!r} does not belong to series "
                f"{department!r}"
            )
        reason = _invalid_reason(original, as_of, config)
        if reason is not None:
            rejected.append(RejectedPoint(original, reason))
            continue
        day = original.date.date() if isinstance(original.date, dt.datetime) else original.date
        point = replace(original, date=day, department=department, source=original.source.strip())
        by_day.setdefault(day, []).append(point)

    kept: list[HeadcountPoint] = []
    for day in sorted(by_day):
        candidates = by_day[day]
        winner = min(candidates, key=lambda p: (_rank(p.source, config), p.source))
        kept.append(winner)
        rejected.extend(
            RejectedPoint(
                p,
                f"duplicate of {day.isoformat()}; kept the point from {winner.source} "
                "(source priority)",
            )
            for p in candidates
            if p is not winner
        )
    return HeadcountSeries(department, tuple(kept), tuple(rejected))


def group_points(
    points: Iterable[HeadcountPoint],
    *,
    as_of: dt.date,
    config: GrowthConfig = DEFAULT_CONFIG,
) -> GroupedSeries:
    """Split mixed points by canonical department and build a series for each.

    Points with an unrecognised department name are rejected (their reason names the
    department), never filed under the company.
    """
    groups: dict[str | None, list[HeadcountPoint]] = {}
    rejected: list[RejectedPoint] = []
    for point in points:
        if point.department is None:
            groups.setdefault(None, []).append(point)
            continue
        canonical = normalize_department(point.department)
        if canonical is None:
            rejected.append(RejectedPoint(point, f"unknown department {point.department!r}"))
            continue
        groups.setdefault(canonical, []).append(point)
    series = {
        key: build_series(members, as_of=as_of, department=key, config=config)
        for key, members in groups.items()
    }
    return GroupedSeries(series, tuple(rejected))
