"""Client and campaign changes write audit entries with diffs (issue #44)."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.test import Client as HttpClient
from django.urls import reverse

from apps.campaigns import services
from apps.campaigns.models import Client
from apps.campaigns.services import ProfileUnchangedError
from apps.core.logging import reset_request_id, set_request_id
from apps.core.models import AuditAction, AuditLog, Job, JobItem
from tests.factories import make_campaign, make_client, make_user

pytestmark = pytest.mark.django_db


def entries(object_type: str, object_id) -> list[AuditLog]:
    return list(AuditLog.objects.for_object(object_type, object_id).order_by("created_at", "id"))


# ------------------------------------------------------------------ clients


def test_create_client_is_audited():
    user = make_user()
    client = services.create_client("  Acme ", "VIP", user=user)

    (row,) = entries("client", client.pk)
    assert row.action == AuditAction.CREATE
    assert row.before is None
    assert row.after == {"name": "Acme", "notes": "VIP", "status": "active", "archived_at": None}
    assert (row.actor, row.client) == (user, client)


def test_update_client_records_only_the_changed_fields():
    user = make_user()
    client = make_client(name="Acme", notes="old")
    token = set_request_id("req-9")
    try:
        services.update_client(client, user, notes="new", name="Acme")
    finally:
        reset_request_id(token)

    client.refresh_from_db()
    assert client.notes == "new"
    (row,) = entries("client", client.pk)
    assert row.action == AuditAction.UPDATE
    assert (row.before, row.after) == ({"notes": "old"}, {"notes": "new"})
    assert (row.actor, row.client, row.request_id) == (user, client, "req-9")


def test_update_client_without_a_change_writes_nothing():
    client = make_client(name="Acme", notes="x")
    services.update_client(client, make_user(), name="Acme", notes="x")
    assert not entries("client", client.pk)


def test_update_client_validates():
    make_client(name="Acme")
    other = make_client(name="Other")
    with pytest.raises(ValidationError):
        services.update_client(other, name="acme")
    with pytest.raises(ValidationError):
        services.update_client(other, name="  ")
    with pytest.raises(ValidationError, match="Unknown"):
        services.update_client(other, status="archived")
    other.refresh_from_db()
    assert other.name == "Other"
    assert not entries("client", other.pk)


def test_create_client_rejects_duplicate_active_names():
    services.create_client("Acme")
    with pytest.raises(ValidationError):
        services.create_client("ACME")
    assert AuditLog.objects.count() == 1


def test_archive_and_restore_client_are_audited():
    user = make_user()
    client = make_client()
    services.archive_client(client, user)
    services.archive_client(client, user)  # idempotent: second call changes nothing
    services.restore_client(client, user)

    archive, restore = entries("client", client.pk)
    assert archive.action == AuditAction.ARCHIVE
    assert archive.before["status"] == "active"
    assert archive.before["archived_at"] is None
    assert archive.after["status"] == "archived"
    assert archive.after["archived_at"]
    assert restore.action == AuditAction.RESTORE
    assert restore.after == {"status": "active", "archived_at": None}
    assert restore.before["status"] == "archived"
    assert not client.is_archived


# ------------------------------------------------------------------ campaigns


def test_create_campaign_is_audited():
    user = make_user()
    campaign = make_campaign(created_by=user)

    (row,) = entries("campaign", campaign.pk)
    assert row.action == AuditAction.CREATE
    assert row.after["name"] == campaign.name
    assert row.after["status"] == "draft"
    assert row.after["profile_version"] == 1
    assert row.after["current_profile_id"] == str(campaign.current_profile_id)
    assert (row.client_id, row.actor_id) == (campaign.client_id, user.pk)


def test_rename_campaign_writes_a_diff():
    user = make_user()
    campaign = make_campaign(name="Q1 Logistics")
    services.update_campaign(campaign, user, name="  Q2 Logistics  ")

    campaign.refresh_from_db()
    assert campaign.name == "Q2 Logistics"
    row = entries("campaign", campaign.pk)[-1]
    assert row.action == AuditAction.UPDATE
    assert (row.before, row.after) == ({"name": "Q1 Logistics"}, {"name": "Q2 Logistics"})
    assert (row.actor, row.client_id) == (user, campaign.client_id)


def test_rename_campaign_rules():
    client = make_client()
    make_campaign(client=client, name="Taken")
    campaign = make_campaign(client=client, name="Mine")
    with pytest.raises(ValidationError, match="already has an active campaign"):
        services.update_campaign(campaign, name="taken")
    with pytest.raises(ValidationError, match="Unknown"):
        services.update_campaign(campaign, status="active")
    services.archive_campaign(campaign)
    with pytest.raises(ValidationError, match="read-only"):
        services.update_campaign(campaign, name="New")


def test_editing_rules_audits_a_reference_to_the_new_version():
    user = make_user()
    campaign = make_campaign()
    profile = services.create_profile_version(
        campaign, {"countries": ["QA"], "custom_rules": "Only large fleets"}, user
    )

    row = entries("campaign_profile", profile.pk)[0]
    assert row.action == AuditAction.CREATE
    assert row.before is None
    assert row.after == {
        "campaign_id": str(campaign.pk),
        "version": 2,
        "previous_version": 1,
        "changed_fields": ["countries", "custom_rules"],
        "change_note": "",
    }
    assert "Only large fleets" not in str(row.after)  # a reference, not a copy of the rules
    assert (row.actor, row.client_id) == (user, campaign.client_id)


def test_unchanged_rules_write_no_audit_entry():
    campaign = make_campaign()
    before = AuditLog.objects.count()
    with pytest.raises(ProfileUnchangedError):
        services.create_profile_version(campaign, {"countries": ["SA", "AE"]})
    assert AuditLog.objects.count() == before


def test_activate_archive_restore_campaign_are_audited():
    user = make_user()
    campaign = make_campaign()
    services.activate_campaign(campaign, user)
    services.archive_campaign(campaign, user)
    services.restore_campaign(campaign, user)

    _create, activate, archive, restore = entries("campaign", campaign.pk)
    assert (activate.action, activate.before, activate.after) == (
        AuditAction.UPDATE,
        {"status": "draft"},
        {"status": "active"},
    )
    assert archive.action == AuditAction.ARCHIVE
    assert archive.before["status"] == "active"
    assert archive.after["status"] == "archived"
    assert restore.action == AuditAction.RESTORE
    assert restore.after["status"] == "draft"
    assert restore.after["archived_at"] is None


def test_failed_change_leaves_no_audit_entry():
    client = make_client()
    client.archive()
    before = AuditLog.objects.count()
    with pytest.raises(ValidationError):
        services.create_campaign(client, "X", {"offer": "x", "countries": ["SA"]})
    assert AuditLog.objects.count() == before


def test_audit_entries_never_hold_secret_fields():
    client = make_client(
        notes="contact password=hunter2 for the portal"
    )  # pragma: allowlist secret
    services.update_client(client, notes="api_key=abc123 rotated")  # pragma: allowlist secret
    dump = " ".join(str(r.before) + str(r.after) for r in AuditLog.objects.all())
    assert "hunter2" not in dump
    assert "abc123" not in dump


# ------------------------------------------------------------------ admin


@pytest.fixture
def admin_http(db) -> tuple[HttpClient, object]:
    admin_user = make_user(is_superuser=True, is_staff=True)
    http = HttpClient()
    http.force_login(admin_user)
    return http, admin_user


def test_admin_edit_and_archive_actions_audit_with_the_acting_user(admin_http):
    http, admin_user = admin_http
    client = make_client(name="Acme", notes="")

    response = http.post(
        reverse("admin:campaigns_client_change", args=[client.pk]),
        {"name": "Acme Ltd", "notes": "renamed", "created_by": ""},
    )
    assert response.status_code == 302
    http.post(
        reverse("admin:campaigns_client_changelist"),
        {"action": "archive_selected", "_selected_action": [str(client.pk)]},
    )

    update, archive = entries("client", client.pk)
    assert update.actor == admin_user
    assert update.after == {"name": "Acme Ltd", "notes": "renamed"}
    assert archive.action == AuditAction.ARCHIVE
    assert archive.actor == admin_user


def test_admin_add_client_is_audited(admin_http):
    http, admin_user = admin_http
    response = http.post(
        reverse("admin:campaigns_client_add"), {"name": "Fresh", "notes": "", "created_by": ""}
    )
    assert response.status_code == 302
    client = Client.objects.get(name="Fresh")
    (row,) = entries("client", client.pk)
    assert row.action == AuditAction.CREATE
    assert row.actor == admin_user
    assert client.created_by == admin_user


def test_admin_campaign_rename_and_restore_are_audited(admin_http):
    http, admin_user = admin_http
    campaign = make_campaign(name="Old")
    inline = {
        "profiles-TOTAL_FORMS": 0,
        "profiles-INITIAL_FORMS": 0,
        "profiles-MIN_NUM_FORMS": 0,
        "profiles-MAX_NUM_FORMS": 1000,
    }
    http.post(
        reverse("admin:campaigns_campaign_change", args=[campaign.pk]), {"name": "New", **inline}
    )
    http.post(
        reverse("admin:campaigns_campaign_changelist"),
        {"action": "archive_selected", "_selected_action": [str(campaign.pk)]},
    )
    http.post(
        reverse("admin:campaigns_campaign_changelist"),
        {"action": "restore_selected", "_selected_action": [str(campaign.pk)]},
    )
    actions = [r.action for r in entries("campaign", campaign.pk)]
    assert actions == ["create", "update", "archive", "restore"]
    assert entries("campaign", campaign.pk)[1].actor == admin_user


def test_job_and_audit_admin_are_read_only(admin_http):
    http, _ = admin_http
    for model in (Job, JobItem, AuditLog):
        modeladmin = site._registry[model]
        request = type("R", (), {"user": make_user(is_superuser=True)})()
        assert not modeladmin.has_add_permission(request)
        assert not modeladmin.has_change_permission(request)
        assert not modeladmin.has_delete_permission(request)
    for name in ("core_job", "core_jobitem", "core_auditlog"):
        assert http.get(reverse(f"admin:{name}_changelist")).status_code == 200
    assert http.get(reverse("admin:core_job_add")).status_code == 403
