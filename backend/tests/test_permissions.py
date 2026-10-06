"""Roles and per-client access control through the demo endpoints (issue #46).

Every role is exercised against every action, plus cross-client id guessing, inactive and
anonymous users, and queryset leak / N+1 checks.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership, revoke_membership
from apps.campaigns.models import Campaign, CampaignProfile
from apps.core.permissions import ClientScopedMixin
from apps.core.roles import Role
from tests.factories import make_campaign, make_client, make_user

pytestmark = [pytest.mark.api, pytest.mark.django_db, pytest.mark.urls("tests.permissions_demo")]

CAMPAIGNS = "/v1/campaigns/"


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def member(role: str, client: Any) -> User:
    user = make_user()
    grant_membership(client, user, role)
    return user


# What each role may do on a campaign of ITS OWN client. (action -> {role: expected status}).
ACTIONS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "list": ("get", "", {}),
    "retrieve": ("get", "{id}/", {}),
    "edit_name": ("patch", "{id}/", {"name": "Renamed"}),
    "edit_rules": ("post", "{id}/rules/", {"offer": "A new offer"}),
    "decide": ("post", "{id}/decide/", {}),
    "archive": ("delete", "{id}/", {}),
}
ALLOWED: dict[str, set[str]] = {
    "list": {"viewer", "reviewer", "manager", "admin"},
    "retrieve": {"viewer", "reviewer", "manager", "admin"},
    "edit_name": {"manager", "admin"},
    "edit_rules": {"manager", "admin"},
    "decide": {"reviewer", "manager", "admin"},
    "archive": {"admin"},
}


def call(api: APIClient, action: str, campaign: Campaign) -> int:
    method, tail, body = ACTIONS[action]
    url = CAMPAIGNS + tail.format(id=campaign.pk)
    return int(getattr(api, method)(url, body, format="json").status_code)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("role", [r.value for r in Role])
def test_role_against_each_action_on_own_client(role: str, action: str):
    campaign = make_campaign()
    api = as_user(member(role, campaign.client))
    expected_ok = role in ALLOWED[action]
    status = call(api, action, campaign)
    if expected_ok:
        assert status in (200, 201, 204), (role, action, status)
    else:
        assert status == 403, (role, action, status)


def test_create_requires_edit_role_in_the_target_client():
    org = make_client()
    for role in Role:
        api = as_user(member(role, org))
        resp = api.post(CAMPAIGNS, {"client": str(org.pk), "name": f"C {role}"}, format="json")
        assert resp.status_code == (201 if role in ("manager", "admin") else 403), role


def test_viewer_cannot_write_anything():
    campaign = make_campaign()
    api = as_user(member(Role.VIEWER, campaign.client))
    for action in ("edit_name", "edit_rules", "decide", "archive"):
        assert call(api, action, campaign) == 403
    assert (
        api.post(CAMPAIGNS, {"client": str(campaign.client_id), "name": "x"}, format="json")
    ).status_code == 403
    campaign.refresh_from_db()
    assert campaign.name != "Renamed"
    assert CampaignProfile.objects.filter(campaign=campaign).count() == 1


def test_reviewer_decides_but_cannot_edit_rules():
    campaign = make_campaign()
    api = as_user(member(Role.REVIEWER, campaign.client))
    assert call(api, "decide", campaign) == 200
    assert call(api, "edit_rules", campaign) == 403
    assert CampaignProfile.objects.filter(campaign=campaign).count() == 1


def test_manager_edits_rules_and_creates_a_new_version():
    campaign = make_campaign()
    api = as_user(member(Role.MANAGER, campaign.client))
    assert call(api, "edit_rules", campaign) == 200
    assert CampaignProfile.objects.filter(campaign=campaign).count() == 2


# ------------------------------------------------------------------ cross-client


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("role", [r.value for r in Role])
def test_other_clients_campaign_is_404_for_every_role_and_action(role: str, action: str):
    mine, theirs = make_campaign(), make_campaign()
    api = as_user(member(role, mine.client))
    if action == "list":
        assert call(api, action, theirs) == 200
        return
    assert call(api, action, theirs) == 404, (role, action)
    theirs.refresh_from_db()
    assert theirs.name != "Renamed"
    assert not theirs.is_archived
    assert CampaignProfile.objects.filter(campaign=theirs).count() == 1


def test_list_only_shows_own_clients_and_never_leaks_others():
    a, b, c = make_campaign(), make_campaign(), make_campaign()
    user = member(Role.VIEWER, a.client)
    grant_membership(b.client, user, Role.MANAGER)
    ids = {row["id"] for row in as_user(user).get(CAMPAIGNS).json()["results"]}
    assert ids == {str(a.pk), str(b.pk)}
    assert str(c.pk) not in ids


def test_404_body_is_identical_for_other_clients_and_nonexistent_ids():
    mine, theirs = make_campaign(), make_campaign()
    api = as_user(member(Role.ADMIN, mine.client))
    other = api.get(f"{CAMPAIGNS}{theirs.pk}/")
    missing = api.get(f"{CAMPAIGNS}{uuid.uuid4()}/")
    assert other.status_code == missing.status_code == 404
    assert other.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
    assert other.json()["error"]["message"] == missing.json()["error"]["message"]


def test_create_in_another_clients_or_unknown_client_is_404():
    mine, theirs = make_client(), make_client()
    api = as_user(member(Role.ADMIN, mine))
    for target in (str(theirs.pk), str(uuid.uuid4()), "not-a-uuid", None):
        resp = api.post(CAMPAIGNS, {"client": target, "name": "Sneaky"}, format="json")
        assert resp.status_code == 404, target
    assert not Campaign.objects.filter(name="Sneaky").exists()


def test_admin_of_one_client_is_only_a_viewer_in_another():
    a, b = make_campaign(), make_campaign()
    user = member(Role.ADMIN, a.client)
    grant_membership(b.client, user, Role.VIEWER)
    api = as_user(user)
    assert call(api, "archive", b) == 403
    assert call(api, "edit_rules", b) == 403
    assert call(api, "retrieve", b) == 200
    assert call(api, "archive", a) == 204


def test_clients_endpoint_is_scoped_by_its_own_id():
    a, b = make_client(), make_client()
    api = as_user(member(Role.VIEWER, a))
    ids = {row["id"] for row in api.get("/v1/clients/").json()["results"]}
    assert ids == {str(a.pk)}
    assert api.get(f"/v1/clients/{b.pk}/").status_code == 404
    assert api.get(f"/v1/clients/{a.pk}/").status_code == 200
    assert api.post("/v1/clients/", {"name": "x"}, format="json").status_code == 403


# ------------------------------------------------------------------ global admin


def test_global_admin_sees_and_does_everything_without_memberships():
    a, b = make_campaign(), make_campaign()
    api = as_user(make_user(is_superuser=True))
    assert {r["id"] for r in api.get(CAMPAIGNS).json()["results"]} == {str(a.pk), str(b.pk)}
    for action in ACTIONS:
        if action != "archive":
            assert call(api, action, b) in (200, 201), action
    assert call(api, "archive", a) == 204
    resp = api.post(CAMPAIGNS, {"client": str(a.client_id), "name": "By admin"}, format="json")
    assert resp.status_code == 201


# ------------------------------------------------------------------ users that must get nothing


def test_user_without_memberships_sees_nothing_and_cannot_write():
    campaign = make_campaign()
    api = as_user(make_user())
    assert api.get(CAMPAIGNS).json()["results"] == []
    assert call(api, "retrieve", campaign) == 404
    assert call(api, "edit_name", campaign) == 404
    assert call(api, "archive", campaign) == 404
    # Creating needs a role somewhere, and fails early without one.
    body = {"client": str(campaign.client_id), "name": "x"}
    assert api.post(CAMPAIGNS, body, format="json").status_code == 403


def test_anonymous_gets_401_on_everything():
    campaign = make_campaign()
    api = APIClient()
    for action in ACTIONS:
        assert call(api, action, campaign) == 401, action


def test_inactive_user_with_a_valid_token_gets_nothing():
    campaign = make_campaign()
    user = member(Role.ADMIN, campaign.client)
    token = AccessToken.for_user(user)
    user.is_active = False
    user.save()
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    for action in ACTIONS:
        assert call(api, action, campaign) == 401, action


def test_inactive_global_admin_gets_nothing():
    campaign = make_campaign()
    admin = make_user(is_superuser=True)
    token = AccessToken.for_user(admin)
    admin.is_active = False
    admin.save()
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api.get(CAMPAIGNS).status_code == 401
    assert call(api, "retrieve", campaign) == 401


def test_revoking_a_membership_takes_effect_on_the_next_request():
    campaign = make_campaign()
    user = member(Role.MANAGER, campaign.client)
    api = as_user(user)
    assert call(api, "retrieve", campaign) == 200
    revoke_membership(campaign.client, user)
    assert call(api, "retrieve", campaign) == 404
    assert api.get(CAMPAIGNS).json()["results"] == []
    assert call(api, "edit_name", campaign) == 404


def test_role_change_takes_effect_on_the_next_request():
    campaign = make_campaign()
    user = member(Role.VIEWER, campaign.client)
    api = as_user(user)
    assert call(api, "edit_name", campaign) == 403
    from apps.campaigns.memberships import change_role

    change_role(campaign.client, user, Role.MANAGER)
    assert call(api, "edit_name", campaign) == 200


# ------------------------------------------------------------------ fail-closed defaults


def test_unlisted_write_action_defaults_to_manage_and_reads_to_read():
    from types import SimpleNamespace

    mixin = ClientScopedMixin()
    get, post = SimpleNamespace(method="GET"), SimpleNamespace(method="POST")
    assert mixin.get_required_level(get).name == "READ"  # type: ignore[arg-type]
    assert mixin.get_required_level(post).name == "MANAGE"  # type: ignore[arg-type]


def test_view_with_unscoped_queryset_is_rejected():
    from rest_framework.test import APIRequestFactory

    from apps.accounts.models import User as UserModel
    from apps.core.permissions import ClientScopedReadOnlyModelViewSet

    class Leaky(ClientScopedReadOnlyModelViewSet):
        queryset = UserModel.objects.all()  # a plain manager: no for_user()

    request = APIRequestFactory().get("/")
    request.user = make_user()
    view = Leaky()
    view.request = request
    with pytest.raises(ImproperlyConfigured):
        view.get_queryset()


# ------------------------------------------------------------------ query counts


def test_list_query_count_does_not_grow_with_rows_or_memberships(django_assert_max_num_queries):
    user = make_user()
    campaigns = [make_campaign() for _ in range(2)]
    grant_membership(campaigns[0].client, user, Role.VIEWER)
    api = as_user(user)
    api.get(CAMPAIGNS)  # warm up
    with django_assert_max_num_queries(6):
        assert len(api.get(CAMPAIGNS).json()["results"]) == 1

    for _ in range(8):
        extra = make_campaign(client=campaigns[0].client)
        grant_membership(make_client(), user, Role.VIEWER)
        assert extra
    with django_assert_max_num_queries(6):
        assert len(api.get(CAMPAIGNS).json()["results"]) == 9


def test_every_for_user_query_is_one_filter_not_python_side(django_assert_num_queries):
    campaign = make_campaign()
    user = member(Role.VIEWER, campaign.client)
    with django_assert_num_queries(1):
        # accessible ids are fetched (1 query); the scoped queryset itself is lazy
        qs = Campaign.objects.for_user(user)
    with django_assert_num_queries(1):
        assert list(qs) == [campaign]
