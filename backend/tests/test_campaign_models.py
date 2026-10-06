"""Client, Campaign and CampaignProfile: fields, DB constraints, archive, tenancy (issue #39)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.campaigns.models import (
    BusinessModel,
    Campaign,
    CampaignProfile,
    CampaignStatus,
    Client,
    ClientStatus,
)
from apps.campaigns.services import create_campaign
from apps.core.base import ImmutableRecordError, TenantMismatchError
from tests.factories import make_campaign, make_client, make_user

pytestmark = pytest.mark.django_db


def _profile(campaign: Campaign, **overrides) -> CampaignProfile:
    """An unsaved profile with valid defaults, for constraint tests."""
    values = {
        "campaign": campaign,
        "version": 99,
        "offer": "Something",
    }
    values.update(overrides)
    return CampaignProfile(**values)


def _violates(obj) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        obj.save()


# ------------------------------------------------------------------ basics


def test_models_use_uuid_primary_keys_and_timestamps():
    campaign = make_campaign()
    for obj in (campaign.client, campaign, campaign.current_profile):
        assert obj.pk.version == 4
    assert campaign.created_at
    assert campaign.updated_at
    assert campaign.current_profile.created_at


def test_new_campaign_has_version_one_as_current_and_is_draft():
    campaign = make_campaign()
    assert campaign.status == CampaignStatus.DRAFT
    assert campaign.current_profile.version == 1
    assert campaign.current_profile.campaign_id == campaign.pk
    assert campaign.profiles.count() == 1


def test_profile_stores_all_rule_fields_with_list_order():
    campaign = make_campaign(profile__preferred_buyer_titles=["CTO", "CIO", "Head of IT"])
    profile = CampaignProfile.objects.get(pk=campaign.current_profile.pk)
    assert profile.countries == ["SA", "AE"]
    assert profile.preferred_buyer_titles == ["CTO", "CIO", "Head of IT"]
    assert profile.company_size_min == 50
    assert profile.company_size_max == 500
    assert profile.business_model == BusinessModel.B2B
    assert profile.outreach_languages == ["en", "ar"]
    assert profile.excluded_company_types == ["non-profit"]
    assert profile.custom_rules.startswith("Prefer")


def test_profile_copies_client_id_from_campaign():
    campaign = make_campaign()
    assert campaign.current_profile.client_id == campaign.client_id


def test_str_representations():
    campaign = make_campaign(name="Q4 push", client__name="SkyLight")
    assert str(campaign.client) == "SkyLight"
    assert str(campaign) == "Q4 push"
    assert str(campaign.current_profile).endswith("v1")


def test_created_by_is_recorded():
    user = make_user()
    campaign = make_campaign(created_by=user)
    assert campaign.created_by == user
    assert campaign.current_profile.created_by == user


# ------------------------------------------------------------------ DB constraints


def test_db_rejects_size_min_greater_than_max():
    campaign = make_campaign()
    _violates(_profile(campaign, company_size_min=100, company_size_max=10))


def test_db_allows_open_ended_size_ranges():
    campaign = make_campaign()
    _profile(campaign, version=2, company_size_min=100).save()
    _profile(campaign, version=3, company_size_max=100).save()
    _profile(campaign, version=4, company_size_min=100, company_size_max=100).save()


def test_db_rejects_negative_size():
    campaign = make_campaign()
    _violates(_profile(campaign, company_size_min=-1))


@pytest.mark.parametrize("version", [0])
def test_db_rejects_version_below_one(version):
    _violates(_profile(make_campaign(), version=version))


def test_db_rejects_duplicate_version_per_campaign_but_allows_it_across_campaigns():
    campaign = make_campaign()
    _violates(_profile(campaign, version=1))
    other = make_campaign()
    assert other.current_profile.version == 1  # same number, different campaign: fine


def test_db_rejects_unknown_business_model_and_empty_offer():
    campaign = make_campaign()
    _violates(_profile(campaign, business_model="b2g"))
    _violates(_profile(campaign, offer=""))


def test_db_rejects_unknown_status_values():
    client = make_client()
    with pytest.raises(IntegrityError), transaction.atomic():
        Client.objects.filter(pk=client.pk).update(status="paused")
    campaign = make_campaign()
    with pytest.raises(IntegrityError), transaction.atomic():
        Campaign.objects.filter(pk=campaign.pk).update(status="paused")


def test_db_requires_status_and_archived_at_to_agree():
    client = make_client()
    with pytest.raises(IntegrityError), transaction.atomic():
        Client.objects.filter(pk=client.pk).update(status=ClientStatus.ARCHIVED)
    campaign = make_campaign()
    with pytest.raises(IntegrityError), transaction.atomic():
        Campaign.objects.filter(pk=campaign.pk).update(archived_at=campaign.created_at)


def test_client_names_unique_case_insensitively_among_active():
    make_client(name="SkyLight")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_client(name="skylight")


def test_campaign_names_unique_per_client_not_across_clients():
    campaign = make_campaign(name="Launch")
    with pytest.raises(ValidationError):
        create_campaign(campaign.client, "launch", {"offer": "x"})
    make_campaign(name="Launch")  # another client: fine


def test_db_unique_campaign_name_backstop():
    campaign = make_campaign(name="Launch")
    other = make_campaign(client=campaign.client, name="Other")
    with pytest.raises(IntegrityError), transaction.atomic():
        Campaign.objects.filter(pk=other.pk).update(name="LAUNCH")


def test_campaign_without_existing_profile_fails_at_commit_time():
    """The FK is deferred (so v1 can follow the campaign) but never skipped."""
    campaign = make_campaign()

    def point_at_nothing() -> None:
        with transaction.atomic():
            Campaign.objects.filter(pk=campaign.pk).update(
                current_profile_id="00000000-0000-4000-8000-000000000000"
            )
            connection.check_constraints()

    with pytest.raises(IntegrityError):
        point_at_nothing()


def test_current_profile_must_be_a_version_of_the_same_campaign_model_clean():
    first, second = make_campaign(), make_campaign()
    first.current_profile = second.current_profile
    with pytest.raises(ValidationError) as exc:
        first.clean()
    assert "current_profile" in exc.value.message_dict


@pytest.mark.skipif(connection.vendor != "postgresql", reason="composite FK is PostgreSQL only")
def test_db_current_profile_must_belong_to_same_campaign():
    first, second = make_campaign(), make_campaign()

    def borrow_other_campaigns_profile() -> None:
        with transaction.atomic():
            Campaign.objects.filter(pk=first.pk).update(current_profile=second.current_profile)
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    with pytest.raises(IntegrityError):
        borrow_other_campaigns_profile()


# ------------------------------------------------------------------ validation


def test_profile_clean_validates_size_countries_and_offer():
    campaign = make_campaign()
    bad = _profile(
        campaign, company_size_min=10, company_size_max=5, countries=["sa", "Saudi"], offer=" "
    )
    with pytest.raises(ValidationError) as exc:
        bad.clean()
    assert {"company_size_max", "countries", "offer"} <= set(exc.value.message_dict)


# ------------------------------------------------------------------ immutability


def test_profile_cannot_be_updated_or_deleted():
    campaign = make_campaign()
    profile = campaign.current_profile
    profile.offer = "changed"
    with pytest.raises(ImmutableRecordError):
        profile.save()
    with pytest.raises(ImmutableRecordError):
        profile.delete()
    with pytest.raises(ImmutableRecordError):
        CampaignProfile.objects.filter(pk=profile.pk).update(offer="changed")
    with pytest.raises(ImmutableRecordError):
        CampaignProfile.objects.filter(pk=profile.pk).delete()
    with pytest.raises(ImmutableRecordError):
        CampaignProfile.objects.bulk_update([profile], ["offer"])
    assert CampaignProfile.objects.get(pk=profile.pk).offer != "changed"


def test_foreign_keys_are_protected_not_cascaded():
    campaign = make_campaign()
    with pytest.raises(ProtectedError):
        campaign.client.delete()
    with pytest.raises(ProtectedError):
        campaign.delete()  # its profile versions (and the pointer) protect it
    assert Campaign.objects.filter(pk=campaign.pk).exists()


# ------------------------------------------------------------------ archive


def test_client_archive_and_restore():
    client = make_client()
    client.archive()
    client.refresh_from_db()
    assert client.is_archived
    assert client.status == ClientStatus.ARCHIVED
    stamp = client.archived_at
    client.archive()  # idempotent
    assert client.archived_at == stamp
    assert list(Client.objects.active()) == []
    assert list(Client.objects.archived()) == [client]
    client.restore()
    restored = Client.objects.get(pk=client.pk)
    assert not restored.is_archived
    assert restored.status == ClientStatus.ACTIVE
    assert list(Client.objects.active()) == [restored]


def test_archiving_a_client_does_not_touch_its_campaigns_or_profiles():
    campaign = make_campaign()
    campaign.client.archive()
    campaign.refresh_from_db()
    assert not campaign.is_archived
    assert campaign.status == CampaignStatus.DRAFT
    assert campaign.profiles.count() == 1


def test_campaign_archive_keeps_history_and_frees_the_name():
    campaign = make_campaign(name="Launch")
    campaign.archive()
    campaign.refresh_from_db()
    assert campaign.status == CampaignStatus.ARCHIVED
    assert campaign.profiles.count() == 1
    assert list(Campaign.objects.active()) == []
    again = create_campaign(campaign.client, "Launch", {"offer": "x"})  # name reusable
    assert again.pk != campaign.pk


def test_campaign_activate_rules():
    campaign = make_campaign()
    campaign.activate()
    campaign.refresh_from_db()
    assert campaign.status == CampaignStatus.ACTIVE

    other = make_campaign(client=campaign.client)
    campaign.client.archive()
    with pytest.raises(ValidationError):
        other.activate()

    campaign.archive()
    with pytest.raises(ValidationError):
        campaign.activate()


def test_campaign_restore_needs_active_client():
    campaign = make_campaign()
    campaign.archive()
    campaign.client.archive()
    with pytest.raises(ValidationError):
        campaign.restore()
    campaign.client.restore()
    campaign.restore()
    campaign.refresh_from_db()
    assert campaign.status == CampaignStatus.DRAFT
    assert campaign.archived_at is None


def test_create_campaign_rejects_archived_client_and_blank_name():
    client = make_client()
    with pytest.raises(ValidationError):
        create_campaign(client, "  ", {"offer": "x"})
    client.archive()
    with pytest.raises(ValidationError):
        create_campaign(client, "Ok", {"offer": "x"})


def test_create_campaign_is_atomic_when_profile_is_invalid():
    client = make_client()
    with pytest.raises(ValidationError):
        create_campaign(client, "Broken", {"offer": ""})
    assert not Campaign.objects.filter(client=client).exists()


# ------------------------------------------------------------------ tenancy


def test_for_user_scopes_by_client():
    campaign_a, campaign_b = make_campaign(), make_campaign()
    admin = make_user(is_superuser=True, is_staff=True)
    regular = make_user()
    inactive_admin = make_user(is_superuser=True, is_active=False)

    assert set(Campaign.objects.for_user(admin)) == {campaign_a, campaign_b}
    assert set(Client.objects.for_user(admin)) == {campaign_a.client, campaign_b.client}
    assert set(CampaignProfile.objects.for_user(admin)) == {
        campaign_a.current_profile,
        campaign_b.current_profile,
    }
    # Fails closed until memberships exist (issue #46).
    for nobody in (regular, inactive_admin, AnonymousUser()):
        assert not Campaign.objects.for_user(nobody).exists()
        assert not Client.objects.for_user(nobody).exists()
        assert not CampaignProfile.objects.for_user(nobody).exists()


def test_for_client_accepts_instance_or_id_and_isolates():
    campaign_a, campaign_b = make_campaign(), make_campaign()
    assert list(Campaign.objects.for_client(campaign_a.client)) == [campaign_a]
    assert list(Campaign.objects.for_client(campaign_b.client_id)) == [campaign_b]
    assert list(CampaignProfile.objects.for_client(campaign_a.client)) == [
        campaign_a.current_profile
    ]
    assert list(Client.objects.for_client(campaign_a.client)) == [campaign_a.client]
    assert list(Campaign.objects.for_client(campaign_a.client).active()) == [campaign_a]


def test_client_id_must_match_parent_and_cannot_move():
    campaign = make_campaign()
    other = make_client()
    with pytest.raises(TenantMismatchError):
        _profile(campaign, client=other).save()
    campaign.client = other
    with pytest.raises(TenantMismatchError):
        campaign.save()
    campaign.refresh_from_db()
    assert campaign.client_id != other.pk


def test_every_profile_client_matches_its_campaign_client():
    campaigns = [make_campaign() for _ in range(3)]
    for campaign in campaigns:
        for profile in campaign.profiles.all():
            assert profile.client_id == campaign.client_id
