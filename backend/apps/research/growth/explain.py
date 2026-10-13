"""Plain-language explanations of growth results (brief section 15: say why, no scores)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from .results import GrowthMethod, GrowthReport, GrowthResult, GrowthStatus, ResolvedPoint

_LABELS = {
    "company": "Headcount",
    "sales": "Sales headcount",
    "bd": "Business development headcount",
    "marketing": "Marketing headcount",
    "commercial": "Commercial headcount",
    "partnerships": "Partnerships headcount",
    "commercial_team": "Commercial team headcount",
}
_HALF_MONTHS = Decimal("0.5")


def count_text(count: Decimal) -> str:
    """``176`` for whole counts, one decimal place otherwise (interpolated baselines)."""
    if count == count.to_integral_value():
        return str(int(count))
    return str(count.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def signed_text(value: Decimal) -> str:
    text = count_text(value)
    return text if text.startswith("-") or text == "0" else f"+{text}"


def months_text(months: int) -> str:
    return f"{months} month" if months == 1 else f"{months} months"


def label_for(result: GrowthResult) -> str:
    base = _LABELS.get(result.scope, f"{result.scope} headcount")
    return f"{base} ({' + '.join(result.components)})" if result.components else base


def _end(point: ResolvedPoint) -> str:
    return f"{count_text(point.count)} on {point.date.isoformat()}"


def _sources(result: GrowthResult) -> str:
    found: list[str] = []
    for point in (result.from_point, result.to_point):
        if point is not None and point.source not in found:
            found.append(point.source)
    return ("source: " if len(found) == 1 else "sources: ") + "; ".join(found)


def _from_text(point: ResolvedPoint) -> str:
    if not point.interpolated:
        return _end(point)
    first, second = point.basis
    return (
        f"an estimated {_end(point)} (interpolated between {first.count} on "
        f"{first.date.isoformat()} and {second.count} on {second.date.isoformat()})"
    )


def explain_result(result: GrowthResult) -> str:
    """One sentence saying what the figure is, which points it rests on, or why it is unknown."""
    label = label_for(result)
    span = months_text(result.months_requested)
    why = result.reason.rstrip(".")
    if result.status == GrowthStatus.INSUFFICIENT_DATA:
        return f"{label} change over {span} is unknown: {why}."
    # Every non-provider, non-insufficient status is built with both points and a change.
    from_point = cast(ResolvedPoint, result.from_point)
    to_point = cast(ResolvedPoint, result.to_point)
    absolute = cast(Decimal, result.absolute_change)
    if result.status in (GrowthStatus.UNDEFINED_BASELINE, GrowthStatus.OUT_OF_RANGE):
        return (
            f"{label} change over {span} has no usable percentage: {why}. "
            f"It went from {_end(from_point)} to {_end(to_point)} "
            f"(change {signed_text(absolute)}, {_sources(result)})."
        )
    percent = cast(Decimal, result.percent)
    if percent > 0:
        verb = f"grew {percent}%"
    elif percent < 0:
        verb = f"fell {abs(percent)}%"
    else:
        verb = "was unchanged"
    if result.method == GrowthMethod.PROVIDER:
        return (
            f"{label} {verb} over {span} according to the provider "
            f"(source: {result.provider_source}; dates and headcounts not supplied)."
        )
    detail = [
        f"from {_from_text(from_point)} to {_end(to_point)}",
        f"change {signed_text(absolute)}",
    ]
    actual = result.months_actual
    if (
        actual is not None
        and not from_point.interpolated
        and abs(actual - result.months_requested) >= _HALF_MONTHS
    ):
        detail.append(f"covers {actual} months")
    detail.append(_sources(result))
    text = f"{label} {verb} over {span} ({', '.join(detail)})."
    if result.status == GrowthStatus.LOW_BASE:
        text += f" Caution: {why}."
    return text


def explain(report: GrowthReport) -> tuple[str, ...]:
    """A sentence per result: company, departments (canonical order), commercial team; 3, 6
    and 12 months each. Unknown results say why they are unknown."""
    return tuple(explain_result(result) for result in report.all_results())
