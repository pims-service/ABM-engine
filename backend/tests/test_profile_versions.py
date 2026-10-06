"""`create_profile_version`: versioning, immutability of old versions, concurrency (issue #39)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.campaigns.models import Campaign, CampaignProfile
from apps.campaigns.services import (
    ProfileUnchangedError,
    create_profile_version,
    normalize_profile_data,
)
from tests.factories import make_campaign, make_user

pytestmark = pytest.mark.django_db


def test_edit_creates_new_version_and_keeps_the_old_one_untouched():
    user = make_user()
    campaign = make_campaign()
    v1 = campaign.current_profile
    snapshot = {f: getattr(v1, f) for f in CampaignProfile.RULE_FIELDS}

    v2 = create_profile_version(
        campaign, {"offer": "New offer", "change_note": "sharper pitch"}, user
    )

    assert v2.version == 2
    assert v2.pk != v1.pk
    assert v2.offer == "New offer"
    assert v2.change_note == "sharper pitch"
    assert v2.created_by == user
    assert v2.client_id == campaign.client_id
    old = CampaignProfile.objects.get(pk=v1.pk)
    assert {f: getattr(old, f) for f in CampaignProfile.RULE_FIELDS} == snapshot
    assert old.version == 1
    assert old.change_note == ""


def test_pointer_moves_atomically_with_the_new_version():
    campaign = make_campaign()
    v2 = create_profile_version(campaign, {"offer": "Two"})
    assert campaign.current_profile == v2  # the passed instance is refreshed
    fresh = Campaign.objects.get(pk=campaign.pk)
    assert fresh.current_profile_id == v2.pk
    v3 = create_profile_version(fresh, {"offer": "Three"})
    assert Campaign.objects.get(pk=campaign.pk).current_profile_id == v3.pk
    assert list(campaign.profiles.order_by("version").values_list("version", flat=True)) == [
        1,
        2,
        3,
    ]


def test_omitted_fields_carry_over_from_current_version():
    campaign = make_campaign()
    v2 = create_profile_version(campaign, {"countries": ["EG"]})
    assert v2.countries == ["EG"]
    assert v2.industries == campaign.profiles.get(version=1).industries
    assert v2.preferred_buyer_titles == ["CIO", "Head of IT", "IT Manager"]
    assert v2.company_size_max == 500


def test_ordered_titles_and_language_changes_are_new_versions():
    campaign = make_campaign()
    v2 = create_profile_version(campaign, {"preferred_buyer_titles": ["IT Manager", "CIO"]})
    assert v2.preferred_buyer_titles == ["IT Manager", "CIO"]
    v3 = create_profile_version(campaign, {"outreach_languages": ["AR", "en"]})
    assert v3.outreach_languages == ["ar", "en"]


def test_identical_rules_do_not_create_a_version():
    campaign = make_campaign()
    with pytest.raises(ProfileUnchangedError):
        create_profile_version(campaign, {"offer": campaign.current_profile.offer})
    with pytest.raises(ProfileUnchangedError):
        create_profile_version(campaign, {})
    assert campaign.profiles.count() == 1


def test_failed_edit_leaves_no_trace():
    campaign = make_campaign()
    with pytest.raises(ValidationError):
        create_profile_version(campaign, {"company_size_min": 900})  # > existing max 500
    with pytest.raises(ValidationError):
        create_profile_version(campaign, {"countries": ["Saudi Arabia"]})
    with pytest.raises(ValidationError):
        create_profile_version(campaign, {"unknown_field": 1})
    with pytest.raises(ValidationError):
        create_profile_version(campaign, {"industries": "Logistics"})
    with pytest.raises(ValidationError):
        create_profile_version(campaign, {"industries": [1, 2]})
    assert campaign.profiles.count() == 1
    assert Campaign.objects.get(pk=campaign.pk).current_profile.version == 1


def test_archived_campaign_rules_are_read_only():
    campaign = make_campaign()
    campaign.archive()
    with pytest.raises(ValidationError):
        create_profile_version(campaign, {"offer": "Nope"})
    assert campaign.profiles.count() == 1


def test_versions_are_numbered_per_campaign():
    first, second = make_campaign(), make_campaign()
    create_profile_version(first, {"offer": "A"})
    create_profile_version(first, {"offer": "B"})
    v2 = create_profile_version(second, {"offer": "C"})
    assert v2.version == 2
    assert first.profiles.count() == 3


def test_service_is_not_fooled_by_a_stale_campaign_instance():
    campaign = make_campaign()
    stale = Campaign.objects.get(pk=campaign.pk)
    create_profile_version(campaign, {"offer": "Two"})
    v3 = create_profile_version(stale, {"offer": "Three"})  # stale.current_profile is v1
    assert v3.version == 3
    assert v3.offer == "Three"


def test_duplicate_version_is_stopped_by_the_database_even_without_the_lock():
    campaign = make_campaign()
    clash = CampaignProfile(campaign=campaign, version=1, offer="Racing writer")
    with pytest.raises(IntegrityError), transaction.atomic():
        clash.save()


def test_normalize_profile_data_trims_dedupes_and_keeps_order():
    data = normalize_profile_data(
        {
            "offer": "  Pitch ",
            "countries": [" sa", "SA", "ae", ""],
            "industries": ["Retail", "retail", " Logistics "],
            "preferred_buyer_titles": ["CIO", "CTO", "cio"],
            "outreach_languages": ["EN", "ar"],
            "custom_rules": None,
        }
    )
    assert data == {
        "offer": "Pitch",
        "countries": ["SA", "AE"],
        "industries": ["Retail", "Logistics"],
        "preferred_buyer_titles": ["CIO", "CTO"],
        "outreach_languages": ["en", "ar"],
        "custom_rules": "",
    }
