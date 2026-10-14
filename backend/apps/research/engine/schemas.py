"""Structured outputs of the two model calls (ADR 0003). Prompts are versioned in prompts.py."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SIGNAL_TYPES = Literal[
    "sales_hiring",
    "bd_hiring",
    "commercial_hiring",
    "partnerships_hiring",
    "headcount_growth",
    "commercial_team_growth",
    "funding",
    "market_expansion",
    "new_leadership",
    "new_office",
    "new_product",
    "major_partnership",
    "other",
]


class SignalFinding(BaseModel):
    type: SIGNAL_TYPES
    summary: str = Field(max_length=300)
    evidence_quote: str = Field(
        max_length=500, description="A verbatim quote from the page that shows the event."
    )
    event_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="The date the page gives for the event, as YYYY-MM-DD.",
    )
    source_url: str = Field(max_length=2000, description="The page URL the quote came from.")


class FactsExtraction(BaseModel):
    industry: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=700)
    headquarters: str | None = Field(default=None, max_length=300)
    employee_count: int | None = Field(
        default=None, ge=1, description="Only if a page states the number of employees."
    )
    business_model: str | None = Field(default=None, max_length=300)
    classification: Literal["b2b", "b2c", "both", "unknown"] = "unknown"
    products_services: str | None = Field(default=None, max_length=700)
    target_customers: str | None = Field(default=None, max_length=500)
    signals: list[SignalFinding] = Field(default_factory=list, max_length=8)


class FitVerdict(BaseModel):
    fit: Literal["strong", "medium", "weak"]
    reasons: list[str] = Field(min_length=1, max_length=6)
    concerns: list[str] = Field(default_factory=list, max_length=6)
    recommendation: Literal["add", "hold", "skip"]
    explanation: str = Field(max_length=800)
    outreach_angle: str | None = Field(
        default=None,
        max_length=500,
        description="Why to contact this account, grounded in the facts. Null when unclear.",
    )
