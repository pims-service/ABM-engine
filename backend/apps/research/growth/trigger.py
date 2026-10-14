"""Does measured growth meet a threshold for a growth signal? (Decision helper only.)

This creates no signal and judges no ICP fit: M5 owns that. It answers "is the growth at least
this big?" for the two growth signal types and returns the evidence as a sentence. Thresholds
are parameters; the defaults below are a starting point, not policy.

Defaults: at least ``+10%`` over ``6`` months *and* an absolute change of at least ``5``
employees. Requiring both keeps tiny companies (a hire is +50%) and huge ones (a few hires are
+0.1%) from triggering on noise. Unknown or unavailable growth never meets the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from .explain import count_text, label_for, months_text, signed_text
from .results import GrowthReport, GrowthResult, GrowthWindows

#: Equal to the matching ``SignalType`` values.
HEADCOUNT_GROWTH = "headcount_growth"
COMMERCIAL_TEAM_GROWTH = "commercial_team_growth"


class TriggerHint(NamedTuple):
    met: bool
    evidence: str


@dataclass(frozen=True)
class GrowthTriggerHint:
    """Configurable thresholds: ``window_months`` is 3, 6 or 12."""

    window_months: int = 6
    min_percent: Decimal = Decimal("10")
    min_absolute_change: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if self.window_months not in (3, 6, 12):
            raise ValueError("window_months must be 3, 6 or 12")
        for value in (self.min_percent, self.min_absolute_change):
            if not isinstance(value, Decimal):
                raise TypeError("thresholds must be Decimal, never float")

    def evaluate(self, report: GrowthReport, signal_type: str) -> TriggerHint:
        """Check the company (``headcount_growth``) or the commercial team
        (``commercial_team_growth``) against the thresholds."""
        if signal_type == HEADCOUNT_GROWTH:
            windows: GrowthWindows = report.company
        elif signal_type == COMMERCIAL_TEAM_GROWTH:
            windows = report.commercial_team
        else:
            raise ValueError(f"unsupported signal type {signal_type!r}")
        return self.evaluate_result(windows.get(self.window_months))

    def evaluate_result(self, result: GrowthResult) -> TriggerHint:
        label = label_for(result)
        span = months_text(result.months_requested)
        if not result.is_available or result.percent is None:
            why = result.reason.rstrip(".")
            return TriggerHint(False, f"{label} growth over {span} could not be assessed: {why}.")
        change = result.absolute_change
        needs = f">= {self.min_percent}% and >= {count_text(self.min_absolute_change)} employees"
        if change is None:
            if self.min_absolute_change > 0:
                return TriggerHint(
                    False,
                    f"{label} growth over {span} is {result.percent}% but the absolute change "
                    f"is unknown, so the {needs} threshold cannot be confirmed.",
                )
            absolute = ""
        else:
            absolute = f" ({signed_text(change)} employees)"
        figure = f"{label} changed {result.percent}%{absolute} over {span}"
        percent_ok = result.percent >= self.min_percent
        change_ok = change is None or change >= self.min_absolute_change
        if percent_ok and change_ok:
            return TriggerHint(True, f"{figure}, meeting the threshold of {needs}.")
        return TriggerHint(False, f"{figure}, below the threshold of {needs}.")
