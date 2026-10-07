"""The "rules summary": a campaign profile in the exact shape later AI prompts consume (#48).

This is a versioned contract. The qualification and message engines (M5, M6) read this dict, not
the model, so a change to the shape must bump ``RULES_SUMMARY_SCHEMA_VERSION`` and be agreed with
them. ``tests/test_campaign_api.py::test_rules_summary_shape_snapshot`` pins the exact output and
``docs/api/openapi.yaml`` documents it (``RulesSummary`` component).

Schema version 1::

    {
      "schema_version": 1,
      "campaign": {"id": "<uuid>", "name": "..."},
      "profile_version": 3,                  # which immutable CampaignProfile this came from
      "offer": "...",
      "targeting": {
        "countries": ["SA", "AE"],           # ISO 3166-1 alpha-2, upper case
        "industries": ["Logistics"],
        "company_size": {"min": 50, "max": 500},   # either may be null (open ended)
        "business_model": "b2b"              # b2b | b2c | both
      },
      "exclusions": {"industries": [...], "company_types": [...]},
      "buyers": {
        "target_departments": ["IT"],
        "preferred_buyer_titles": ["CIO", "Head of IT"]   # most preferred first
      },
      "outreach": {"languages": ["en", "ar"]},     # lower case codes
      "custom_rules": "free text",
      "structured_rules": []                 # reserved, always empty in version 1
    }

``structured_rules`` is reserved for a future machine-readable rule list. Version 1 has no
storage for it, so the key is present but always ``[]``.
"""

from __future__ import annotations

from typing import Any

from .models import Campaign, CampaignProfile

RULES_SUMMARY_SCHEMA_VERSION = 1


def build_rules_summary(
    campaign: Campaign, profile: CampaignProfile | None = None
) -> dict[str, Any]:
    """Rules summary of ``campaign`` for ``profile`` (default: its current version)."""
    profile = profile or campaign.current_profile
    return {
        "schema_version": RULES_SUMMARY_SCHEMA_VERSION,
        "campaign": {"id": str(campaign.pk), "name": campaign.name},
        "profile_version": profile.version,
        "offer": profile.offer,
        "targeting": {
            "countries": list(profile.countries),
            "industries": list(profile.industries),
            "company_size": {"min": profile.company_size_min, "max": profile.company_size_max},
            "business_model": profile.business_model,
        },
        "exclusions": {
            "industries": list(profile.excluded_industries),
            "company_types": list(profile.excluded_company_types),
        },
        "buyers": {
            "target_departments": list(profile.target_departments),
            "preferred_buyer_titles": list(profile.preferred_buyer_titles),
        },
        "outreach": {"languages": list(profile.outreach_languages)},
        "custom_rules": profile.custom_rules,
        "structured_rules": [],
    }
