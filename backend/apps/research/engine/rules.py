"""Hard rules applied after the model: the campaign's exclusions are not up to the model.

Pure functions over plain values, so they are easy to test. They only ever make an outcome more
cautious, and every change adds a plain-words reason, so the card shows why.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from apps.campaigns.models import BusinessModel, CampaignProfile

_RANK = {"weak": 0, "medium": 1, "strong": 2}


@dataclass
class Verdict:
    fit: str
    recommendation: str
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


def _hits(needles: Sequence[str], *texts: str | None) -> list[str]:
    haystack = " ".join(t for t in texts if t).casefold()
    return [n for n in needles if n.strip() and n.strip().casefold() in haystack]


def apply_rules(
    verdict: Verdict,
    profile: CampaignProfile,
    *,
    industry: str | None,
    description: str | None,
    classification: str | None,
    employee_count: int | None,
) -> Verdict:
    """Return ``verdict`` adjusted by the campaign's exclusions, business model and size."""
    # Excluded industries and company types: a match on what the company says about itself.
    excluded = _hits(profile.excluded_industries, industry, description) + _hits(
        profile.excluded_company_types, industry, description
    )
    if excluded:
        verdict.fit = "weak"
        verdict.recommendation = "skip"
        verdict.concerns.insert(0, f"Matches an excluded industry or type: {excluded[0]}.")

    # Business model: a B2B-only campaign does not target a B2C company, and the other way round.
    wanted = profile.business_model
    one_sided = wanted in (BusinessModel.B2B, BusinessModel.B2C)
    if classification in ("b2b", "b2c") and one_sided and classification != wanted:
        verdict.fit = "weak"
        if verdict.recommendation == "add":
            verdict.recommendation = "hold"
        looks, targets = classification.upper(), str(wanted).upper()
        verdict.concerns.insert(0, f"The company looks {looks}; this campaign targets {targets}.")

    # Size: outside the range caps the fit at medium (the number is only used when found).
    if employee_count is not None:
        low, high = profile.company_size_min, profile.company_size_max
        if (low is not None and employee_count < low) or (
            high is not None and employee_count > high
        ):
            verdict.concerns.append(
                f"{employee_count} employees is outside the campaign's size range."
            )
            if _RANK.get(verdict.fit, 0) > _RANK["medium"]:
                verdict.fit = "medium"

    # A weak fit is never an "add".
    if verdict.fit == "weak" and verdict.recommendation == "add":
        verdict.recommendation = "hold"
    return verdict
