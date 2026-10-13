"""Mapping of a ``GrowthReport`` onto ``CompanyResearch`` columns.

``department_growth`` JSON (version 1), every number a string because JSON has no Decimal:

    {
      "version": 1,
      "as_of": "2026-10-13",
      "company": {"3m": WINDOW, "6m": WINDOW, "12m": WINDOW},
      "departments": {"sales": {"3m": WINDOW, ...}, "bd": {...}},   # canonical names
      "commercial_team": {"components": ["sales", "marketing"], "3m": WINDOW, ...}
    }

    WINDOW = {
      "months_requested": 12,
      "months_actual": "12.00" | null,
      "status": "ok" | "low_base" | "insufficient_data" | "undefined_baseline" | "out_of_range",
      "method": "observed" | "interpolated" | "provider" | null,
      "percent": "-12.00" | null,
      "absolute_change": "-21" | null,
      "from": {"date": "2025-10-13", "count": "176", "source": "...", "interpolated": false}
              | null,
      "to": {"date": "2026-10-13", "count": "155", "source": "...", "interpolated": false} | null,
      "reason": "" | "why it is unknown or flagged"
    }

``department_growth`` is ``None`` when every result is ``insufficient_data`` (nothing found).
The three ``headcount_change_*`` columns hold the company-level percent, ``None`` when unknown.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypedDict

from .results import GrowthReport, GrowthResult, GrowthStatus, GrowthWindows, ResolvedPoint

DEPARTMENT_GROWTH_VERSION = 1


class ResearchFields(TypedDict):
    headcount_change_3m: Decimal | None
    headcount_change_6m: Decimal | None
    headcount_change_12m: Decimal | None
    department_growth: dict[str, Any] | None


def _point(point: ResolvedPoint | None) -> dict[str, Any] | None:
    if point is None:
        return None
    return {
        "date": point.date.isoformat(),
        "count": _text(point.count),
        "source": point.source,
        "interpolated": point.interpolated,
    }


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return str(int(value))
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def window_json(result: GrowthResult) -> dict[str, Any]:
    return {
        "months_requested": result.months_requested,
        "months_actual": None if result.months_actual is None else str(result.months_actual),
        "status": result.status.value,
        "method": None if result.method is None else result.method.value,
        "percent": None if result.percent is None else str(result.percent),
        "absolute_change": _text(result.absolute_change),
        "from": _point(result.from_point),
        "to": _point(result.to_point),
        "reason": result.reason,
    }


def _windows_json(windows: GrowthWindows) -> dict[str, Any]:
    return {f"{r.months_requested}m": window_json(r) for r in windows.results()}


def to_research_fields(report: GrowthReport) -> ResearchFields:
    """The ``CompanyResearch`` growth columns for a report (see the module docstring)."""
    company = report.company
    fields: ResearchFields = {
        "headcount_change_3m": company.m3.percent,
        "headcount_change_6m": company.m6.percent,
        "headcount_change_12m": company.m12.percent,
        "department_growth": None,
    }
    if all(r.status == GrowthStatus.INSUFFICIENT_DATA for r in report.all_results()):
        return fields
    commercial = report.commercial_team
    fields["department_growth"] = {
        "version": DEPARTMENT_GROWTH_VERSION,
        "as_of": report.as_of.isoformat(),
        "company": _windows_json(company),
        "departments": {name: _windows_json(w) for name, w in report.departments.items()},
        "commercial_team": {
            "components": list(commercial.m3.components),
            **_windows_json(commercial),
        },
    }
    return fields
