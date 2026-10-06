"""ClientMembership model, membership-backed tenancy and the grant/change/revoke services (#46)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.campaigns.memberships import change_role, grant_membership, revoke_membership
from apps.campaigns.models import Campaign, Client, ClientMembership
from apps.core import tenancy
from apps.core.base import TenantMismatchError
from apps.core.roles import Level, Role, role_allows
from tests.factories import make_campaign, make_client, make_user

pytestmark = pytest.mark.django_db


def test_unique_per_user_and_client():
    client, user = make_client(), make_user()
    grant_membership(client, user, Role.VIEWER)
    with pytest.raises(IntegrityError), transaction.atomic():
        ClientMembership.objects.create(client=client, user=user, role=Role.ADMIN)


def test_role_is_checked_by_the_database():
    client, user = make_client(), make_user()
    with pytest.raises(IntegrityError), transaction.atomic():
        ClientMembership.objects.create(client=client, user=user, role="owner")


def test_membership_cannot_move_to_another_client():
    client, other, user = make_client(), make_client(), make_user()
    membership = grant_membership(client, user, Role.VIEWER)
    membership.client = other
    with pytest.raises(TenantMismatchError):
        membership.save()


def test_accessible_client_ids_follow_memberships():
    a, b, c = make_client(), make_client(), make_client()
    user = make_user()
    grant_membership(a, user, Role.VIEWER)
    grant_membership(b, user, Role.MANAGER)
    assert tenancy.accessible_client_ids(user) == {a.pk, b.pk}
    assert c.pk not in (tenancy.accessible_client_ids(user) or set())
    assert tenancy.client_roles(user) == {a.pk: "viewer", b.pk: "manager"}


def test_for_user_scopes_every_tenant_model_by_membership():
    campaign_a, campaign_b = make_campaign(), make_campaign()
    user = make_user()
    grant_membership(campaign_a.client, user, Role.VIEWER)
    assert list(Campaign.objects.for_user(user)) == [campaign_a]
    assert list(Client.objects.for_user(user)) == [campaign_a.client]
    assert list(ClientMembership.objects.for_user(user)) == [
        ClientMembership.objects.get(user=user)
    ]
    assert campaign_b not in Campaign.objects.for_user(user)


def test_global_admin_sees_everything_and_needs_no_membership():
    campaign_a, campaign_b = make_campaign(), make_campaign()
    admin = make_user(is_superuser=True)
    assert tenancy.accessible_client_ids(admin) is None
    assert set(Campaign.objects.for_user(admin)) == {campaign_a, campaign_b}
    assert tenancy.has_client_level(admin, campaign_a.client_id, Level.MANAGE)


def test_inactive_anonymous_and_inactive_admin_see_nothing_even_with_membership():
    client = make_client()
    inactive = make_user(is_active=False)
    inactive_admin = make_user(is_superuser=True, is_active=False)
    grant_membership(client, inactive, Role.ADMIN)
    for nobody in (inactive, inactive_admin, AnonymousUser()):
        assert tenancy.accessible_client_ids(nobody) == set()
        assert not Client.objects.for_user(nobody).exists()
        assert not tenancy.has_client_level(nobody, client.pk, Level.READ)


def test_revoked_membership_stops_access_immediately_and_regrant_restores_row():
    client, user = make_client(), make_user()
    first = grant_membership(client, user, Role.REVIEWER)
    revoke_membership(client, user)
    assert tenancy.accessible_client_ids(user) == set()
    assert tenancy.role_in_client(user, client.pk) is None
    first.refresh_from_db()
    assert first.is_archived
    again = grant_membership(client, user, Role.VIEWER)
    assert again.pk == first.pk
    assert not again.is_archived
    assert tenancy.role_in_client(user, client.pk) == Role.VIEWER
    assert ClientMembership.objects.filter(user=user, client=client).count() == 1


def test_grant_on_active_membership_updates_role():
    client, user = make_client(), make_user()
    grant_membership(client, user, Role.VIEWER)
    grant_membership(client, user, Role.MANAGER)
    assert tenancy.role_in_client(user, client.pk) == Role.MANAGER


def test_change_role_and_revoke_require_an_active_membership():
    client, user = make_client(), make_user()
    with pytest.raises(ValidationError):
        change_role(client, user, Role.MANAGER)
    with pytest.raises(ValidationError):
        revoke_membership(client, user)
    grant_membership(client, user, Role.VIEWER)
    assert change_role(client, user, Role.REVIEWER).role == Role.REVIEWER
    revoke_membership(client, user)
    with pytest.raises(ValidationError):
        change_role(client, user, Role.MANAGER)


def test_unknown_role_and_archived_client_are_rejected():
    client, user = make_client(), make_user()
    with pytest.raises(ValidationError):
        grant_membership(client, user, "owner")
    client.archive()
    with pytest.raises(ValidationError):
        grant_membership(client, user, Role.VIEWER)


def test_only_client_admins_and_global_admins_may_manage_memberships():
    client, other = make_client(), make_client()
    target = make_user()
    cases = {
        Role.VIEWER: False,
        Role.REVIEWER: False,
        Role.MANAGER: False,
        Role.ADMIN: True,
    }
    for role, allowed in cases.items():
        actor = make_user()
        grant_membership(client, actor, role)
        if allowed:
            grant_membership(client, target, Role.VIEWER, actor=actor)
            revoke_membership(client, target, actor=actor)
        else:
            with pytest.raises(PermissionDenied):
                grant_membership(client, target, Role.VIEWER, actor=actor)
    # An admin of one client has no power over another client.
    client_admin = make_user()
    grant_membership(client, client_admin, Role.ADMIN)
    with pytest.raises(PermissionDenied):
        grant_membership(other, target, Role.VIEWER, actor=client_admin)
    grant_membership(other, target, Role.VIEWER, actor=make_user(is_superuser=True))


def test_admin_grant_change_and_revoke(client):
    from django.urls import reverse

    client.force_login(make_user(is_superuser=True, is_staff=True))
    org, member = make_client(), make_user()

    resp = client.post(
        reverse("admin:campaigns_clientmembership_add"),
        {"user": member.pk, "client": org.pk, "role": Role.VIEWER},
    )
    assert resp.status_code == 302
    membership = ClientMembership.objects.get(user=member, client=org)
    assert membership.role == Role.VIEWER

    change = reverse("admin:campaigns_clientmembership_change", args=(membership.pk,))
    assert client.post(change, {"role": Role.MANAGER}).status_code == 302
    membership.refresh_from_db()
    assert ClientMembership.objects.get(pk=membership.pk).role == Role.MANAGER

    changelist = reverse("admin:campaigns_clientmembership_changelist")
    client.post(changelist, {"action": "archive_selected", "_selected_action": [membership.pk]})
    membership.refresh_from_db()
    assert membership.is_archived
    client.post(changelist, {"action": "restore_selected", "_selected_action": [membership.pk]})
    membership.refresh_from_db()
    assert not membership.is_archived
    assert ClientMembership.objects.get(pk=membership.pk).role == Role.MANAGER
    assert client.post(change.replace("change/", "delete/")).status_code == 403


def test_role_matrix_levels():
    expected = {
        Role.VIEWER: {Level.READ},
        Role.REVIEWER: {Level.READ, Level.DECIDE},
        Role.MANAGER: {Level.READ, Level.DECIDE, Level.EDIT},
        Role.ADMIN: set(Level),
    }
    for role, levels in expected.items():
        assert {lv for lv in Level if role_allows(role, lv)} == levels
    assert not role_allows(None, Level.READ)
    assert not role_allows("bogus", Level.READ)
