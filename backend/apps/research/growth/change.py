"""The growth formula for one window.

    percent = (latest - baseline) / baseline * 100, rounded to 2 places, ROUND_HALF_UP
    (halves go away from zero: 0.005 -> 0.01, -0.005 -> -0.01)

* ``latest`` is the newest point on or before ``as_of``, at most ``latest_max_age_days`` old.
* ``baseline`` is the point closest to ``as_of - months`` (calendar months, end-of-month
  clamped) within the tolerance window. Only points older than ``latest`` qualify.
* With no point inside the tolerance, the baseline is linearly interpolated between the last
  point before and the first point after the target, but only when those two are at most
  ``max_gap_days`` apart. Otherwise the result is ``insufficient_data``; nothing is guessed.
* A zero baseline gives ``undefined_baseline``, a percentage outside +-9999.99 gives
  ``out_of_range`` (both with ``percent=None``) and a baseline under ``low_base_threshold``
  employees is computed but flagged ``low_base``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .config import DEFAULT_CONFIG, GrowthConfig
from .explain import count_text
from .months import months_between, shift_months
from .results import (
    GrowthMethod,
    GrowthResult,
    GrowthStatus,
    ResolvedPoint,
)
from .series import HeadcountPoint, HeadcountSeries

TWO_PLACES = Decimal("0.01")
#: Largest magnitude of a ``Decimal(6, 2)`` column.
MAX_PERCENT = Decimal("9999.99")
_PRECISION = 60


def quantize_percent(value: Decimal) -> Decimal:
    """Round to 2 decimal places, halves away from zero."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _resolved(point: HeadcountPoint) -> ResolvedPoint:
    return ResolvedPoint(point.date, Decimal(point.count), point.source)


def _insufficient(
    scope: str,
    months: int,
    reason: str,
    *,
    components: tuple[str, ...] = (),
    to_point: ResolvedPoint | None = None,
    provider_source: str | None = None,
) -> GrowthResult:
    return GrowthResult(
        scope=scope,
        months_requested=months,
        status=GrowthStatus.INSUFFICIENT_DATA,
        reason=reason,
        to_point=to_point,
        components=components,
        provider_source=provider_source,
    )


def _interpolate(before: HeadcountPoint, after: HeadcountPoint, target: dt.date) -> ResolvedPoint:
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        span = Decimal((after.date - before.date).days)
        elapsed = Decimal((target - before.date).days)
        count = Decimal(before.count) + Decimal(after.count - before.count) * elapsed / span
    source = before.source if before.source == after.source else f"{before.source}, {after.source}"
    return ResolvedPoint(target, count, source, interpolated=True, basis=(before, after))


def compute_change(
    series: HeadcountSeries,
    months: int,
    *,
    as_of: dt.date,
    tolerance_days: int | None = None,
    config: GrowthConfig = DEFAULT_CONFIG,
    scope: str | None = None,
    components: tuple[str, ...] = (),
) -> GrowthResult:
    """Headcount change over the last ``months`` months as of ``as_of``.

    ``tolerance_days`` overrides the configured tolerance for this window. ``scope`` labels the
    result (default: the series department, or ``company``).
    """
    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        raise ValueError("months must be a positive integer")
    label = scope or series.department or "company"
    tolerance = config.tolerance_for(months) if tolerance_days is None else tolerance_days
    if tolerance < 0:
        raise ValueError("tolerance_days must not be negative")

    def unknown(reason: str, to_point: ResolvedPoint | None = None) -> GrowthResult:
        return _insufficient(label, months, reason, components=components, to_point=to_point)

    points = [p for p in series.points if p.date <= as_of]
    if not points:
        return unknown(f"no headcount points on or before {as_of.isoformat()}")
    latest = points[-1]
    age = (as_of - latest.date).days
    if age > config.latest_max_age_days:
        return unknown(
            f"latest headcount point is {age} days old ({latest.date.isoformat()}); "
            f"at most {config.latest_max_age_days} days allowed"
        )
    target = shift_months(as_of, -months)
    if target >= latest.date:
        return unknown(
            f"latest headcount point ({latest.date.isoformat()}) is not after the window "
            f"start {target.isoformat()}",
            to_point=_resolved(latest),
        )

    earlier = [p for p in points if p.date < latest.date]
    near = [p for p in earlier if abs((p.date - target).days) <= tolerance]
    baseline: ResolvedPoint
    if near:
        chosen = min(near, key=lambda p: (abs((p.date - target).days), p.date))
        baseline = _resolved(chosen)
        method = GrowthMethod.OBSERVED
    else:
        before = [p for p in points if p.date < target]
        after = [p for p in points if p.date > target]
        reason = f"no headcount point within {tolerance} days of {target.isoformat()}"
        if not before or not after:
            return unknown(reason, to_point=_resolved(latest))
        lower, upper = before[-1], after[0]
        gap = (upper.date - lower.date).days
        if gap > config.max_gap_days:
            return unknown(
                f"{reason}; the points around it are {gap} days apart "
                f"(at most {config.max_gap_days} for interpolation)",
                to_point=_resolved(latest),
            )
        baseline = _interpolate(lower, upper, target)
        method = GrowthMethod.INTERPOLATED

    to_point = _resolved(latest)
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        absolute = to_point.count - baseline.count
        percent = quantize_percent(absolute / baseline.count * 100) if baseline.count != 0 else None
    found = GrowthResult(
        scope=label,
        months_requested=months,
        status=GrowthStatus.OK,
        absolute_change=absolute,
        from_point=baseline,
        to_point=to_point,
        months_actual=months_between(baseline.date, to_point.date),
        method=method,
        components=components,
    )
    if percent is None:
        return replace(
            found,
            status=GrowthStatus.UNDEFINED_BASELINE,
            reason=(
                f"baseline headcount on {baseline.date.isoformat()} is 0, "
                "so a percentage is undefined"
            ),
        )
    if abs(percent) > MAX_PERCENT:
        return replace(
            found,
            status=GrowthStatus.OUT_OF_RANGE,
            reason=f"change of {percent}% is outside the storable range (+-{MAX_PERCENT}%)",
        )
    if baseline.count < config.low_base_threshold:
        reason = (
            f"baseline of {count_text(baseline.count)} employees is below "
            f"{config.low_base_threshold}; a few hires look like large growth"
        )
        return replace(found, status=GrowthStatus.LOW_BASE, percent=percent, reason=reason)
    return replace(found, percent=percent)


def provider_result(
    percent: Decimal | None,
    months: int,
    *,
    source: str,
    scope: str = "company",
) -> GrowthResult:
    """Wrap a percentage supplied by a provider (no series, so no dates and no absolute change).

    ``None`` stays unknown. Floats are refused (``TypeError``), the value is rounded to 2 places
    and a magnitude beyond +-9999.99 is flagged ``out_of_range``.
    """
    if percent is None:
        return _insufficient(scope, months, "provider gave no percentage", provider_source=source)
    if not isinstance(percent, Decimal):
        raise TypeError("provider percentages must be Decimal, never float")
    if not percent.is_finite():
        return _insufficient(scope, months, "provider percentage is not a number")
    rounded = quantize_percent(percent)
    if abs(rounded) > MAX_PERCENT:
        return GrowthResult(
            scope=scope,
            months_requested=months,
            status=GrowthStatus.OUT_OF_RANGE,
            method=GrowthMethod.PROVIDER,
            reason=f"provider change of {rounded}% is outside the storable range",
            provider_source=source,
        )
    return GrowthResult(
        scope=scope,
        months_requested=months,
        status=GrowthStatus.OK,
        percent=rounded,
        method=GrowthMethod.PROVIDER,
        reason="provider-supplied percentage; dates and counts were not given",
        provider_source=source,
    )
