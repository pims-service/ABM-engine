"""Company, department and commercial-team growth in one report.

The commercial-team roll-up is derived, never provider data: the departments that have data
(sales, bd, marketing, commercial, partnerships) are summed into one series, and only at dates
where *every* component has a point within ``commercial_align_days``. A summed point is dated
at the latest of its component points and cites all their sources. With fewer than
``commercial_min_components`` departments, or no aligned dates, the roll-up is
``insufficient_data`` with a reason. A roll-up of a single department would just repeat it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from .change import compute_change, provider_result
from .config import DEFAULT_CONFIG, WINDOWS, GrowthConfig
from .departments import DEPARTMENT_ORDER, normalize_department
from .results import (
    COMMERCIAL_TEAM,
    COMPANY,
    GrowthReport,
    GrowthResult,
    GrowthStatus,
    GrowthWindows,
)
from .series import (
    HeadcountPoint,
    HeadcountSeries,
    RejectedPoint,
    build_series,
    group_points,
)


@dataclass(frozen=True)
class ProviderGrowth:
    """Percentages a provider supplied directly (``{3: Decimal('-4.5'), 12: None}``).

    Used only as a fallback for windows the series cannot answer (``insufficient_data``);
    ``source`` is its provenance reference.
    """

    source: str
    changes: Mapping[int, Decimal | None]


def _windows(results: Iterable[GrowthResult]) -> GrowthWindows:
    m3, m6, m12 = results
    return GrowthWindows(m3, m6, m12)


def _scope_windows(
    series: HeadcountSeries | None,
    provider: ProviderGrowth | None,
    *,
    scope: str,
    as_of: dt.date,
    config: GrowthConfig,
    components: tuple[str, ...] = (),
    empty_reason: str,
) -> GrowthWindows:
    out: list[GrowthResult] = []
    for months in WINDOWS:
        if series is None or not series.points:
            result = GrowthResult(
                scope=scope,
                months_requested=months,
                status=GrowthStatus.INSUFFICIENT_DATA,
                reason=empty_reason,
            )
        else:
            result = compute_change(
                series, months, as_of=as_of, config=config, scope=scope, components=components
            )
        if (
            result.status == GrowthStatus.INSUFFICIENT_DATA
            and provider is not None
            and provider.changes.get(months) is not None
        ):
            result = provider_result(
                provider.changes[months], months, source=provider.source, scope=scope
            )
        out.append(result)
    return _windows(out)


def _commercial_series(
    members: Mapping[str, HeadcountSeries], config: GrowthConfig
) -> tuple[HeadcountSeries | None, str]:
    """Sum the component series at aligned dates. Returns ``(series, reason_if_none)``."""
    names = tuple(n for n in DEPARTMENT_ORDER if n in members and members[n].points)
    if len(names) < config.commercial_min_components:
        return None, (
            f"commercial-team roll-up needs at least {config.commercial_min_components} "
            f"departments with data; have {len(names)}"
            + (f" ({', '.join(names)})" if names else "")
        )
    window = dt.timedelta(days=config.commercial_align_days)
    combos: dict[tuple[HeadcountPoint, ...], HeadcountPoint] = {}
    for anchor_name in names:
        for anchor in members[anchor_name].points:
            chosen: list[HeadcountPoint] = []
            for name in names:
                near = [p for p in members[name].points if abs(p.date - anchor.date) <= window]
                if not near:
                    break
                chosen.append(min(near, key=lambda p: (abs(p.date - anchor.date), p.date)))
            else:
                key = tuple(chosen)
                sources = sorted({p.source for p in chosen})
                combos[key] = HeadcountPoint(
                    date=max(p.date for p in chosen),
                    count=sum(p.count for p in chosen),
                    source=", ".join(sources),
                    department=COMMERCIAL_TEAM,
                )
    by_day: dict[dt.date, tuple[int, HeadcountPoint]] = {}
    for key, summed in combos.items():
        spread = (max(p.date for p in key) - min(p.date for p in key)).days
        current = by_day.get(summed.date)
        if current is None or spread < current[0]:
            by_day[summed.date] = (spread, summed)
    points = tuple(by_day[day][1] for day in sorted(by_day))
    if not points:
        return None, (
            f"no dates where {', '.join(names)} all have a headcount point "
            f"within {config.commercial_align_days} days of each other"
        )
    return HeadcountSeries(COMMERCIAL_TEAM, points), ""


def compute_growth(
    series_by_department: Mapping[str | None, HeadcountSeries],
    as_of: dt.date,
    *,
    config: GrowthConfig = DEFAULT_CONFIG,
    provider: Mapping[str | None, ProviderGrowth] | None = None,
) -> GrowthReport:
    """Growth at 3, 6 and 12 months for the company (key ``None``), every department given and
    the commercial-team roll-up.

    Keys are department names (any spelling ``normalize_department`` knows); two keys that map
    to the same department are merged. Unknown keys are listed in
    ``GrowthReport.ignored_departments``. ``provider`` supplies direct percentages used only
    where the series cannot answer.
    """
    ignored: list[str] = []
    rejected: list[RejectedPoint] = []
    merged: dict[str | None, list[HeadcountSeries]] = {}
    for key, series in series_by_department.items():
        canonical = key if key is None else normalize_department(key)
        if key is not None and canonical is None:
            ignored.append(key)
            continue
        merged.setdefault(canonical, []).append(series)

    resolved: dict[str | None, HeadcountSeries] = {}
    for key, group in merged.items():
        rejected.extend(r for s in group for r in s.rejected)
        if len(group) == 1:
            resolved[key] = group[0]
        else:
            resolved[key] = build_series(
                (p for s in group for p in s.points), as_of=as_of, department=key, config=config
            )
            rejected.extend(resolved[key].rejected)

    providers: dict[str | None, ProviderGrowth] = {}
    for key, given in (provider or {}).items():
        canonical = key if key is None else normalize_department(key)
        if key is not None and canonical is None:
            ignored.append(key)
            continue
        providers.setdefault(canonical, given)

    company = _scope_windows(
        resolved.get(None),
        providers.get(None),
        scope=COMPANY,
        as_of=as_of,
        config=config,
        empty_reason="no company-level headcount data",
    )
    departments: dict[str, GrowthWindows] = {}
    for name in DEPARTMENT_ORDER:
        if name in resolved or name in providers:
            departments[name] = _scope_windows(
                resolved.get(name),
                providers.get(name),
                scope=name,
                as_of=as_of,
                config=config,
                empty_reason=f"no {name} headcount data",
            )
    members = {n: s for n, s in resolved.items() if n is not None}
    rollup, why = _commercial_series(members, config)
    components = tuple(n for n in DEPARTMENT_ORDER if n in members and members[n].points)
    commercial = _scope_windows(
        rollup,
        None,
        scope=COMMERCIAL_TEAM,
        as_of=as_of,
        config=config,
        components=components if rollup is not None else (),
        empty_reason=why,
    )
    return GrowthReport(
        as_of=as_of,
        company=company,
        departments=departments,
        commercial_team=commercial,
        rejected=tuple(rejected),
        ignored_departments=tuple(ignored),
    )


def growth_from_points(
    points: Iterable[HeadcountPoint],
    as_of: dt.date,
    *,
    config: GrowthConfig = DEFAULT_CONFIG,
    provider: Mapping[str | None, ProviderGrowth] | None = None,
) -> GrowthReport:
    """Convenience for collectors: group mixed points by department, build the series and
    compute the report. Points rejected on the way are in ``GrowthReport.rejected``."""
    grouped = group_points(points, as_of=as_of, config=config)
    report = compute_growth(grouped.series, as_of, config=config, provider=provider)
    return replace(report, rejected=(*grouped.rejected, *report.rejected))
