"""Versioned prompts. Bump the version on every wording change: it is stored with each result."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from apps.campaigns.models import CampaignProfile
from apps.integrations.framework import PromptSpec
from apps.integrations.llm import UNTRUSTED_SYSTEM_NOTE, wrap_untrusted
from apps.research.models import Signal

EXTRACT_VERSION = "1"
ASSESS_VERSION = "1"
SCHEMA_VERSION = "1"

MAX_PAGE_CHARS = 24_000


def extraction_prompt(name: str, pages: Sequence[tuple[str, str]], today: date) -> PromptSpec:
    """Facts and dated signals from the company's own pages. ``pages`` is (url, text) pairs."""
    body = "\n\n".join(f"### {url}\n{text}" for url, text in pages)
    system = (
        "You extract facts about one company from its own website pages. Use only what the pages "
        "say. If a page does not state a fact, return null for it: never guess, never use outside "
        "knowledge. For `signals` return only events the pages describe with an explicit date "
        "(news, press, hiring announcements, funding, partnerships, new offices, new products, "
        "leadership changes, expansion). Each signal needs a verbatim `evidence_quote` copied "
        "exactly from a page, the `event_date` the page gives (YYYY-MM-DD) and the page URL. "
        "Skip anything without a stated date. Do not treat an open careers page without dates as "
        "a signal. " + UNTRUSTED_SYSTEM_NOTE
    )
    user = (
        f"Today is {today.isoformat()}.\n"
        f"Company name given by the user: {name}\n\n"
        + wrap_untrusted(body, "company_pages", max_chars=MAX_PAGE_CHARS)
    )
    return PromptSpec(name="company_facts", version=EXTRACT_VERSION, system=system, user=user)


def _rules(profile: CampaignProfile) -> str:
    def lst(items: Sequence[str]) -> str:
        return ", ".join(items) if items else "not specified"

    size = "not specified"
    low, high = profile.company_size_min, profile.company_size_max
    if low is not None and high is not None:
        size = f"{low} to {high} employees"
    elif low is not None:
        size = f"at least {low} employees"
    elif high is not None:
        size = f"up to {high} employees"
    return (
        f"Offer: {profile.offer}\n"
        f"Countries: {lst(profile.countries)}\n"
        f"Industries: {lst(profile.industries)}\n"
        f"Company size: {size}\n"
        f"Business model: {profile.business_model}\n"
        f"Excluded industries: {lst(profile.excluded_industries)}\n"
        f"Excluded company types: {lst(profile.excluded_company_types)}\n"
        f"Target departments: {lst(profile.target_departments)}\n"
        f"Preferred buyer titles: {lst(profile.preferred_buyer_titles)}\n"
        f"Custom rules: {profile.custom_rules or 'none'}"
    )


def assessment_prompt(
    profile: CampaignProfile,
    company_name: str,
    facts: dict[str, object],
    signals: Sequence[Signal],
) -> PromptSpec:
    """ICP fit and an add/hold/skip recommendation, judged against the campaign rules."""
    system = (
        "You judge whether a company is a good prospect for a campaign. ICP FIT (strong, medium, "
        "weak) is about whether the company is fundamentally a good prospect for the offer. A "
        "TRIGGER is a separate question (is there a reason to contact now). A company does NOT "
        "need a trigger to qualify: never lower the fit or recommend skip because there is no "
        "trigger. Use only the campaign rules and the company facts given. Facts that are null "
        "are unknown: say so in `concerns`, do not assume them. Give 1 to 6 short plain reasons. "
        "`recommendation` is add, hold or skip and `explanation` says why in one or two "
        "sentences. `outreach_angle` states why to contact this account in one sentence using "
        "only the given facts, or null if the facts do not support one. " + UNTRUSTED_SYSTEM_NOTE
    )
    fact_lines = "\n".join(f"- {key}: {value}" for key, value in facts.items())
    signal_lines = (
        "\n".join(f"- {s.type} ({s.event_date}): {s.evidence}" for s in signals) or "none found"
    )
    user = f"Campaign rules:\n{_rules(profile)}\n\nCompany: {company_name}\n" + wrap_untrusted(
        f"Facts found on the company's website:\n{fact_lines}\n\nDated events:\n{signal_lines}",
        "company_facts",
    )
    return PromptSpec(name="icp_fit", version=ASSESS_VERSION, system=system, user=user)
