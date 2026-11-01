"""The campaign rules compiler (#109): same rules, same text and hash; any change, a new hash."""

from __future__ import annotations

from typing import Any

import pytest

from apps.campaigns.models import CampaignProfile
from apps.research.engine.prompts import assessment_prompt
from apps.research.engine.rules_compiler import compile_rules, rules_hash, rules_text

SKYLIGHT: dict[str, Any] = {
    "offer": "B2B outbound lead generation",
    "countries": ["SA"],
    "industries": ["Financial Services", "SaaS"],
    "company_size_min": 10,
    "company_size_max": 500,
    "business_model": "b2b",
    "excluded_industries": ["Government"],
    "excluded_company_types": ["Non-profit"],
    "target_departments": ["Sales", "Business Development"],
    "preferred_buyer_titles": ["VP BD", "Head of BD", "CEO"],
    "outreach_languages": ["ar", "en"],
    "custom_rules": "Prefer companies with an existing BD team.",
}

OTHER_CLIENT: dict[str, Any] = {
    "offer": "Cloud security audits",
    "countries": ["DE", "NL"],
    "industries": ["Healthcare"],
    "company_size_min": 200,
    "company_size_max": None,
    "business_model": "both",
    "excluded_industries": [],
    "excluded_company_types": [],
    "target_departments": ["IT"],
    "preferred_buyer_titles": ["CISO", "CIO"],
    "outreach_languages": ["en"],
    "custom_rules": "",
}


def profile(**overrides: Any) -> CampaignProfile:
    """An unsaved profile: the compiler only reads its rule fields."""
    return CampaignProfile(**{**SKYLIGHT, **overrides})


def test_the_same_rules_always_compile_to_the_same_text_and_hash() -> None:
    first = compile_rules(profile())
    second = compile_rules(profile())
    assert first == second
    assert len(first.hash) == 64


def test_the_version_and_other_non_rule_fields_do_not_change_the_hash() -> None:
    assert rules_hash(profile(version=1, change_note="a")) == rules_hash(
        profile(version=7, change_note="b")
    )


CHANGES: list[tuple[str, Any]] = [
    ("offer", "A different offer"),
    ("countries", ["AE"]),
    ("industries", ["SaaS", "Financial Services"]),  # same items, other order
    ("company_size_min", 11),
    ("company_size_max", None),
    ("business_model", "b2c"),
    ("excluded_industries", []),
    ("excluded_company_types", ["Non-profit", "Public company"]),
    ("target_departments", ["Sales"]),
    ("preferred_buyer_titles", ["CEO", "VP BD", "Head of BD"]),  # reordered
    ("outreach_languages", ["en"]),  # the text does not mention languages; the hash must
    ("custom_rules", "Something else."),
]


def test_every_rule_field_is_covered_by_the_change_cases() -> None:
    assert {name for name, _ in CHANGES} == set(CampaignProfile.RULE_FIELDS)


@pytest.mark.parametrize(("field", "value"), CHANGES)
def test_changing_any_rule_field_changes_the_hash(field: str, value: Any) -> None:
    assert rules_hash(profile(**{field: value})) != rules_hash(profile())


def test_two_different_clients_compile_to_different_rules() -> None:
    skylight = compile_rules(profile())
    other = compile_rules(CampaignProfile(**OTHER_CLIENT))
    assert skylight.hash != other.hash
    assert skylight.text != other.text
    assert "Financial Services" in skylight.text
    assert "Healthcare" in other.text
    assert "CISO" in other.text
    assert "Financial Services" not in other.text


def test_open_ended_size_and_empty_lists_are_written_plainly() -> None:
    text = rules_text(CampaignProfile(**OTHER_CLIENT))
    assert "Company size: at least 200 employees" in text
    assert "Excluded industries: not specified" in text
    assert "Custom rules: none" in text


def test_the_assessment_prompt_uses_the_compiled_text() -> None:
    spec = assessment_prompt(profile(), "Acme", {"industry": "SaaS"}, [])
    assert compile_rules(profile()).text in spec.user
