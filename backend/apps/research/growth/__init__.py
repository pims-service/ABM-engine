"""Headcount growth calculator (issue #97): pure Python, Decimal arithmetic, no DB, no network.

Feed ``HeadcountPoint`` observations to ``growth_from_points`` (or build series yourself and call
``compute_growth``), then use ``explain`` for the why, ``to_research_fields`` for the
``CompanyResearch`` columns and ``GrowthTriggerHint`` for M5. See docs/research/headcount-growth.md.
"""

from __future__ import annotations

from .change import MAX_PERCENT, compute_change, provider_result, quantize_percent
from .config import (
    DEFAULT_CONFIG,
    DEFAULT_SOURCE_PRIORITY,
    DEFAULT_TOLERANCE_DAYS,
    WINDOWS,
    GrowthConfig,
)
from .departments import DEPARTMENT_ORDER, normalize_department
from .explain import explain, explain_result
from .fields import DEPARTMENT_GROWTH_VERSION, ResearchFields, to_research_fields
from .months import months_between, shift_months
from .report import ProviderGrowth, compute_growth, growth_from_points
from .results import (
    COMMERCIAL_TEAM,
    COMPANY,
    GrowthMethod,
    GrowthReport,
    GrowthResult,
    GrowthStatus,
    GrowthWindows,
    ResolvedPoint,
)
from .series import (
    GroupedSeries,
    HeadcountPoint,
    HeadcountSeries,
    RejectedPoint,
    build_series,
    group_points,
)
from .trigger import (
    COMMERCIAL_TEAM_GROWTH,
    HEADCOUNT_GROWTH,
    GrowthTriggerHint,
    TriggerHint,
)

__all__ = [
    "COMMERCIAL_TEAM",
    "COMMERCIAL_TEAM_GROWTH",
    "COMPANY",
    "DEFAULT_CONFIG",
    "DEFAULT_SOURCE_PRIORITY",
    "DEFAULT_TOLERANCE_DAYS",
    "DEPARTMENT_GROWTH_VERSION",
    "DEPARTMENT_ORDER",
    "HEADCOUNT_GROWTH",
    "MAX_PERCENT",
    "WINDOWS",
    "GroupedSeries",
    "GrowthConfig",
    "GrowthMethod",
    "GrowthReport",
    "GrowthResult",
    "GrowthStatus",
    "GrowthTriggerHint",
    "GrowthWindows",
    "HeadcountPoint",
    "HeadcountSeries",
    "ProviderGrowth",
    "RejectedPoint",
    "ResearchFields",
    "ResolvedPoint",
    "TriggerHint",
    "build_series",
    "compute_change",
    "compute_growth",
    "explain",
    "explain_result",
    "group_points",
    "growth_from_points",
    "months_between",
    "normalize_department",
    "provider_result",
    "quantize_percent",
    "shift_months",
    "to_research_fields",
]
