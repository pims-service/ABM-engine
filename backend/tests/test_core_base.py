"""Shared bases in apps/core: StringListField, archive mixin, tenant helpers, admin wiring."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse

from apps.campaigns.models import Campaign, CampaignProfile, Client
from apps.core import tenancy
from apps.core.fields import StringListField
from tests.factories import UserFactory, make_campaign, make_user


class TestStringListField:
    def test_to_python(self):
        field = StringListField()
        assert field.to_python(None) == []
        assert field.to_python(("a", "b")) == ["a", "b"]
        assert field.to_python('["x", "y"]') == ["x", "y"]
        for bad in ("not json", [1], {"a": 1}, '{"a": 1}'):
            with pytest.raises(ValidationError):
                field.to_python(bad)

    def test_null_is_rejected_by_system_check(self):
        field = StringListField(null=True)
        field.set_attributes_from_name("x")
        assert [e.id for e in field.check()] == ["core.E001"]
        clean = StringListField()
        clean.set_attributes_from_name("x")
        assert not [e for e in clean.check() if isinstance(e, checks.Error)]

    def test_db_type_and_prep_value(self):
        field = StringListField()
        assert field.db_type(connection) == (
            "text[]" if connection.vendor == "postgresql" else "text"
        )
        assert field.get_db_prep_value(None, connection) is None
        countries = CampaignProfile._meta.get_field("countries")
        assert countries.value_to_string(CampaignProfile(offer="x", countries=["SA"])) == '["SA"]'

    @pytest.mark.django_db
    def test_roundtrip_keeps_order_duplicates_and_unicode(self):
        campaign = make_campaign()
        raw = CampaignProfile(
            campaign=campaign, version=2, offer="x", industries=["b", "a", "b", "مصرف"]
        )
        raw.save()  # bypasses the service, which would de-duplicate
        loaded = CampaignProfile.objects.get(pk=raw.pk)
        assert loaded.industries == ["b", "a", "b", "مصرف"]
        assert loaded.countries == []


class TestTenancyHelpers:
    def test_accessible_client_ids(self):
        assert tenancy.accessible_client_ids(UserFactory.build(is_superuser=True)) is None
        regular = UserFactory.build()
        assert tenancy.accessible_client_ids(regular) == set()
        assert tenancy.is_global_admin(regular) is False


@pytest.mark.django_db
class TestAdmin:
    def test_models_are_registered_and_profile_is_read_only(self, rf):
        request = rf.get("/")
        request.user = make_user(is_superuser=True, is_staff=True)
        profile_admin = site._registry[CampaignProfile]
        campaign = make_campaign()
        assert not profile_admin.has_add_permission(request)
        assert not profile_admin.has_change_permission(request, campaign.current_profile)
        assert not profile_admin.has_delete_permission(request, campaign.current_profile)
        assert "offer" in profile_admin.get_readonly_fields(request, campaign.current_profile)
        assert not site._registry[Campaign].has_add_permission(request)
        assert not site._registry[Campaign].has_delete_permission(request)
        assert not site._registry[Client].has_delete_permission(request)

    def test_admin_pages_render_and_archive_actions_work(self, client):
        admin_user = make_user(is_superuser=True, is_staff=True)
        client.force_login(admin_user)
        campaign = make_campaign()
        for name, args in (
            ("admin:campaigns_client_changelist", ()),
            ("admin:campaigns_client_change", (campaign.client.pk,)),
            ("admin:campaigns_campaign_changelist", ()),
            ("admin:campaigns_campaign_change", (campaign.pk,)),
            ("admin:campaigns_campaignprofile_changelist", ()),
            ("admin:campaigns_campaignprofile_change", (campaign.current_profile.pk,)),
        ):
            assert client.get(reverse(name, args=args)).status_code == 200, name

        for model, obj in ((Campaign, campaign), (Client, campaign.client)):
            label = model._meta.model_name
            url = reverse(f"admin:campaigns_{label}_changelist")
            client.post(url, {"action": "archive_selected", "_selected_action": [obj.pk]})
            obj.refresh_from_db()
            assert obj.is_archived
            client.post(url, {"action": "restore_selected", "_selected_action": [obj.pk]})
            obj.refresh_from_db()
            assert not obj.is_archived

    def test_profile_post_is_forbidden(self, client):
        client.force_login(make_user(is_superuser=True, is_staff=True))
        campaign = make_campaign()
        url = reverse("admin:campaigns_campaignprofile_change", args=(campaign.current_profile.pk,))
        assert client.post(url, {"offer": "hacked"}).status_code == 403


def test_client_manager_is_tenant_scoped_on_its_own_id():
    assert Client.objects.all().tenant_lookup == "id"
    assert Campaign.objects.all().tenant_lookup == "client_id"
