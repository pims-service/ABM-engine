"""Client CRUD API (issue #47): happy paths, validation, role x action matrix, isolation."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns import services
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Client, ClientMembership
from tests.factories import make_campaign, make_client, make_user

pytestmark = [pytest.mark.api, pytest.mark.django_db]

URL = "/api/v1/clients/"


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def member(role: str, client: Client) -> User:
    user = make_user()
    grant_membership(client, user, role)
    return user


def admin_of(*clients: Client) -> User:
    user = make_user()
    for client in clients:
        grant_membership(client, user, "admin")
    return user


def names(response: Any) -> list[str]:
    return [row["name"] for row in response.json()["results"]]


# ------------------------------------------------------------------ happy paths


def test_global_admin_creates_and_reads_client() -> None:
    root = make_user(is_superuser=True)
    api = as_user(root)
    response = api.post(URL, {"name": "  Acme  ", "notes": "VIP"}, format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme"
    assert body["notes"] == "VIP"
    assert body["status"] == "active"
    assert body["archived_at"] is None
    got = api.get(f"{URL}{body['id']}/")
    assert got.status_code == 200
    assert got.json() == body
    assert Client.objects.get(pk=body["id"]).created_by == root


def test_creator_becomes_admin_and_sees_the_client() -> None:
    existing = make_client()
    user = admin_of(existing)
    api = as_user(user)
    response = api.post(URL, {"name": "Fresh"}, format="json")
    assert response.status_code == 201
    assert ClientMembership.objects.get(user=user, client_id=response.json()["id"]).role == "admin"
    assert set(names(api.get(URL))) == {existing.name, "Fresh"}


def test_update_put_and_patch() -> None:
    client = make_client(name="Old", notes="n")
    api = as_user(member("manager", client))
    patched = api.patch(f"{URL}{client.pk}/", {"notes": "new notes"}, format="json")
    assert patched.status_code == 200
    assert patched.json()["notes"] == "new notes"
    assert patched.json()["name"] == "Old"
    put = api.put(f"{URL}{client.pk}/", {"name": "Renamed", "notes": ""}, format="json")
    assert put.status_code == 200
    assert (put.json()["name"], put.json()["notes"]) == ("Renamed", "")
    client.refresh_from_db()
    assert client.name == "Renamed"


def test_put_requires_name() -> None:
    client = make_client()
    api = as_user(member("admin", client))
    response = api.put(f"{URL}{client.pk}/", {"notes": "x"}, format="json")
    assert response.status_code == 400
    assert "name" in response.json()["error"]["details"]


def test_status_and_archived_at_are_read_only() -> None:
    client = make_client()
    api = as_user(member("admin", client))
    response = api.patch(
        f"{URL}{client.pk}/",
        {"status": "archived", "archived_at": "2020-01-01T00:00:00Z"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["archived_at"] is None


def test_no_delete_method() -> None:
    client = make_client()
    api = as_user(member("admin", client))
    assert api.delete(f"{URL}{client.pk}/").status_code == 405
    assert Client.objects.filter(pk=client.pk).exists()


# ------------------------------------------------------------------ archive / restore


def test_archive_hides_from_default_list_but_keeps_data() -> None:
    client = make_client(name="Gone", notes="keep me")
    campaign = make_campaign(client=client)
    keep = make_client(name="Stays")
    user = admin_of(client, keep)
    api = as_user(user)

    response = api.post(f"{URL}{client.pk}/archive/")
    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert response.json()["archived_at"] is not None

    assert names(api.get(URL)) == ["Stays"]
    assert names(api.get(URL, {"archived": "true"})) == ["Gone"]
    assert set(names(api.get(URL, {"archived": "all"}))) == {"Gone", "Stays"}
    assert names(api.get(URL, {"status": "archived"})) == ["Gone"]
    assert api.get(f"{URL}{client.pk}/").json()["notes"] == "keep me"
    campaign.refresh_from_db()  # data is untouched
    assert campaign.client_id == client.pk

    again = api.post(f"{URL}{client.pk}/archive/")  # idempotent
    assert again.status_code == 200
    assert again.json()["archived_at"] == response.json()["archived_at"]


def test_archived_client_is_read_only_until_restored() -> None:
    client = make_client(name="Frozen")
    api = as_user(member("admin", client))
    api.post(f"{URL}{client.pk}/archive/")
    response = api.patch(f"{URL}{client.pk}/", {"notes": "x"}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "non_field_errors" in response.json()["error"]["details"]

    restored = api.post(f"{URL}{client.pk}/restore/")
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert restored.json()["archived_at"] is None
    assert names(api.get(URL)) == ["Frozen"]
    assert api.post(f"{URL}{client.pk}/restore/").status_code == 200  # idempotent


def test_restore_fails_when_name_was_taken() -> None:
    old = make_client(name="Taken")
    root = as_user(make_user(is_superuser=True))
    root.post(f"{URL}{old.pk}/archive/")
    assert root.post(URL, {"name": "taken"}, format="json").status_code == 201
    response = root.post(f"{URL}{old.pk}/restore/")
    assert response.status_code == 400
    assert "name" in response.json()["error"]["details"]
    old.refresh_from_db()
    assert old.is_archived


def test_archived_name_can_be_reused() -> None:
    old = make_client(name="Reuse")
    root = as_user(make_user(is_superuser=True))
    root.post(f"{URL}{old.pk}/archive/")
    assert root.post(URL, {"name": "REUSE"}, format="json").status_code == 201


# ------------------------------------------------------------------ validation errors


@pytest.mark.parametrize("name", ["Acme", "acme", "ACME", " aCmE "])
def test_duplicate_name_is_a_field_error_in_the_envelope(name: str) -> None:
    make_client(name="Acme")
    api = as_user(make_user(is_superuser=True))
    response = api.post(URL, {"name": name}, format="json")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed."
    assert error["details"]["name"] == [services.CLIENT_NAME_TAKEN]
    assert "request_id" in error


def test_duplicate_name_on_rename_but_same_name_is_fine() -> None:
    make_client(name="Taken")
    client = make_client(name="Mine")
    api = as_user(make_user(is_superuser=True))
    clash = api.patch(f"{URL}{client.pk}/", {"name": "TAKEN"}, format="json")
    assert clash.status_code == 400
    assert "name" in clash.json()["error"]["details"]
    same = api.patch(f"{URL}{client.pk}/", {"name": "MINE"}, format="json")  # own name, new case
    assert same.status_code == 200
    assert same.json()["name"] == "MINE"


@pytest.mark.parametrize(
    "payload", [{}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": "x" * 201}]
)
def test_invalid_names(payload: dict[str, Any]) -> None:
    api = as_user(make_user(is_superuser=True))
    response = api.post(URL, payload, format="json")
    assert response.status_code == 400
    assert "name" in response.json()["error"]["details"]


def test_notes_must_be_text() -> None:
    api = as_user(make_user(is_superuser=True))
    response = api.post(URL, {"name": "N", "notes": None}, format="json")
    assert response.status_code == 400
    assert "notes" in response.json()["error"]["details"]


def test_invalid_list_filters() -> None:
    api = as_user(make_user(is_superuser=True))
    for params in ({"status": "bogus"}, {"archived": "maybe"}):
        response = api.get(URL, params)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"
        assert set(response.json()["error"]["details"]) == set(params)


# ------------------------------------------------------------------ list: filter/search/order


def test_search_ordering_pagination() -> None:
    root = as_user(make_user(is_superuser=True))
    for n in ("Bravo Ltd", "alpha corp", "Charlie Bravo", "Delta"):
        make_client(name=n)
    assert len(names(root.get(URL))) == 4
    assert set(names(root.get(URL, {"search": "bravo"}))) == {"Bravo Ltd", "Charlie Bravo"}
    assert names(root.get(URL, {"search": "zzz"})) == []
    asc = names(root.get(URL, {"ordering": "name"}))
    desc = names(root.get(URL, {"ordering": "-name"}))
    assert desc == asc[::-1]  # database collation decides the order, not Python
    page = root.get(URL, {"page_size": "2", "ordering": "name"}).json()
    assert page["count"] == 4
    assert len(page["results"]) == 2
    assert page["next"] is not None


def test_status_filter_active() -> None:
    a = make_client(name="A")
    b = make_client(name="B")
    root = as_user(make_user(is_superuser=True))
    root.post(f"{URL}{b.pk}/archive/")
    assert names(root.get(URL, {"status": "active"})) == [a.name]
    assert names(root.get(URL, {"status": "active", "archived": "all"})) == [a.name]
    assert names(root.get(URL, {"status": "archived", "archived": "all"})) == [b.name]


# ------------------------------------------------------------------ roles x actions

# action -> (method, path suffix, body)
ACTIONS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "list": ("get", "", {}),
    "retrieve": ("get", "{id}/", {}),
    "update_patch": ("patch", "{id}/", {"notes": "edited"}),
    "update_put": ("put", "{id}/", {"name": "Put Name"}),
    "archive": ("post", "{id}/archive/", {}),
    "restore": ("post", "{id}/restore/", {}),
    "delete": ("delete", "{id}/", {}),
}
ALLOWED = {
    "list": {"viewer", "reviewer", "manager", "admin", "global"},
    "retrieve": {"viewer", "reviewer", "manager", "admin", "global"},
    "update_patch": {"manager", "admin", "global"},
    "update_put": {"manager", "admin", "global"},
    "archive": {"admin", "global"},
    "restore": {"admin", "global"},
}


def call(api: APIClient, action: str, client: Client) -> int:
    method, suffix, body = ACTIONS[action]
    kwargs: dict[str, Any] = {"format": "json"} if body else {}
    return int(
        getattr(api, method)(URL + suffix.format(id=client.pk), body or None, **kwargs).status_code
    )


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager", "admin", "global"])
@pytest.mark.parametrize("action", [a for a in ACTIONS if a != "delete"])
def test_role_matrix(role: str, action: str) -> None:
    client = make_client()
    user = make_user(is_superuser=True) if role == "global" else member(role, client)
    status = call(as_user(user), action, client)
    assert status == (200 if role in ALLOWED[action] else 403), (role, action)


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager", "admin", "global"])
def test_delete_is_never_allowed(role: str) -> None:
    client = make_client()
    user = make_user(is_superuser=True) if role == "global" else member(role, client)
    assert call(as_user(user), "delete", client) in (403, 405)
    assert Client.objects.filter(pk=client.pk).exists()


@pytest.mark.parametrize(
    ("role", "expected"),
    [("viewer", 403), ("reviewer", 403), ("manager", 403), ("admin", 201), ("global", 201)],
)
def test_create_needs_manage_somewhere(role: str, expected: int) -> None:
    home = make_client()
    user = make_user(is_superuser=True) if role == "global" else member(role, home)
    response = as_user(user).post(URL, {"name": f"New by {role}"}, format="json")
    assert response.status_code == expected
    assert Client.objects.filter(name=f"New by {role}").exists() is (expected == 201)


def test_create_without_any_membership_is_forbidden() -> None:
    response = as_user(make_user()).post(URL, {"name": "Nope"}, format="json")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


# ------------------------------------------------------------------ isolation


@pytest.mark.parametrize("action", [a for a in ACTIONS if a not in ("list", "delete")])
@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_other_clients_id_is_404_for_every_action(role: str, action: str) -> None:
    mine = make_client(name="Mine")
    theirs = make_client(name="Theirs")
    api = as_user(member(role, mine))
    response = call(api, action, theirs)
    assert response == 404
    guessed = api.get(f"{URL}{uuid.uuid4()}/")
    assert guessed.status_code == 404
    assert guessed.json()["error"]["code"] == "not_found"
    theirs.refresh_from_db()
    assert theirs.notes == ""
    assert not theirs.is_archived


def test_cross_client_404_for_archived_client_too() -> None:
    theirs = make_client()
    theirs.archive()
    api = as_user(admin_of(make_client()))
    assert api.get(f"{URL}{theirs.pk}/").status_code == 404
    assert api.post(f"{URL}{theirs.pk}/restore/").status_code == 404


def test_list_only_contains_own_clients() -> None:
    mine, other = make_client(name="Mine"), make_client(name="Other")
    api = as_user(member("viewer", mine))
    response = api.get(URL)
    assert names(response) == ["Mine"]
    assert response.json()["count"] == 1
    assert api.get(URL, {"search": "Other"}).json()["count"] == 0
    assert other.name not in response.content.decode()


def test_revoked_membership_loses_access_on_next_request() -> None:
    client = make_client()
    user = member("admin", client)
    api = as_user(user)
    assert api.get(f"{URL}{client.pk}/").status_code == 200
    ClientMembership.objects.filter(user=user).update(archived_at="2020-01-01T00:00:00Z")
    assert api.get(f"{URL}{client.pk}/").status_code == 404
    assert names(api.get(URL)) == []


def test_anonymous_and_inactive_users() -> None:
    client = make_client()
    assert APIClient().get(URL).status_code == 401
    assert APIClient().post(URL, {"name": "x"}, format="json").status_code == 401
    inactive = member("admin", client)
    api = as_user(inactive)
    inactive.is_active = False
    inactive.save()
    assert api.get(URL).status_code in (401, 403)
    root = make_user(is_superuser=True)
    api = as_user(root)
    root.is_active = False
    root.save()
    assert api.get(URL).status_code in (401, 403)


# ------------------------------------------------------------------ queries


def test_list_query_count_does_not_grow_with_clients() -> None:
    user = make_user()
    for _ in range(2):
        grant_membership(make_client(), user, "viewer")
    api = as_user(user)
    api.get(URL)  # warm up
    with CaptureQueriesContext(connection) as small:
        assert api.get(URL).json()["count"] == 2
    for _ in range(15):
        grant_membership(make_client(), user, "viewer")
    with CaptureQueriesContext(connection) as large:
        assert api.get(URL).json()["count"] == 17
    assert len(large) == len(small)
    assert len(large) <= 5

    root = as_user(make_user(is_superuser=True))
    with CaptureQueriesContext(connection) as rooted:
        root.get(URL)
    assert len(rooted) <= 4


# ------------------------------------------------------------------ services


def test_service_create_client_rejects_blank_and_duplicate() -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        services.create_client("   ")
    services.create_client("Svc")
    with pytest.raises(ValidationError):
        services.create_client("svc")


def test_service_update_client_rejects_unknown_fields() -> None:
    from django.core.exceptions import ValidationError

    client = services.create_client("Svc2")
    with pytest.raises(ValidationError):
        services.update_client(client, {"status": "archived"})
    services.update_client(client, {"notes": "n"})
    client.refresh_from_db()
    assert client.notes == "n"
