"""Compile a campaign profile into the rules text the AI is given, and a hash of the rules (#109).

``compile_rules(profile)`` is a pure function of the profile's rule fields
(``CampaignProfile.RULE_FIELDS``): the same rules always give the same text and the same hash,
and changing any rule field changes the hash. Nothing here knows about a particular client; two
campaigns differ only because their rules differ.

The text is what ``assessment_prompt`` puts in front of the model. The hash covers every rule
field, including the ones the text does not mention (the outreach languages), so it identifies
the rules a result was judged against. Lists keep their order: the first preferred buyer title is
the most preferred, so reordering them is a change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from apps.campaigns.models import CampaignProfile


@dataclass(frozen=True)
class CompiledRules:
    text: str
    hash: str


def _list(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "not specified"


def _size(profile: CampaignProfile) -> str:
    low, high = profile.company_size_min, profile.company_size_max
    if low is not None and high is not None:
        return f"{low} to {high} employees"
    if low is not None:
        return f"at least {low} employees"
    if high is not None:
        return f"up to {high} employees"
    return "not specified"


def rules_text(profile: CampaignProfile) -> str:
    """The rules as plain lines for the prompt."""
    return (
        f"Offer: {profile.offer}\n"
        f"Countries: {_list(profile.countries)}\n"
        f"Industries: {_list(profile.industries)}\n"
        f"Company size: {_size(profile)}\n"
        f"Business model: {profile.business_model}\n"
        f"Excluded industries: {_list(profile.excluded_industries)}\n"
        f"Excluded company types: {_list(profile.excluded_company_types)}\n"
        f"Target departments: {_list(profile.target_departments)}\n"
        f"Preferred buyer titles: {_list(profile.preferred_buyer_titles)}\n"
        f"Custom rules: {profile.custom_rules or 'none'}"
    )


def rules_hash(profile: CampaignProfile) -> str:
    """SHA-256 of the canonical JSON of every rule field."""
    values: dict[str, Any] = {name: getattr(profile, name) for name in CampaignProfile.RULE_FIELDS}
    canonical = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_rules(profile: CampaignProfile) -> CompiledRules:
    return CompiledRules(text=rules_text(profile), hash=rules_hash(profile))
