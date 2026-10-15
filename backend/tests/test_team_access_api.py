"""Client team management and changing your own password."""

from __future__ import annotations

from typing import Any

import pytest

from apps.accounts.models import User
from apps.campaigns.models import ClientMembership
from tests.factories import make_client, make_user
from tests.test_company_api import api_for, as_user

pytestmark = [pytest.mark.api, pytest.mark.django_db]

PASSWORD = "Correct-Horse-9-battery"  # pragma: allowlist secret
NEW_PASSWORD = "Another-Strong-Pass-42"  # pragma: allowlist secret


def url(client: Any, action: str) -> str:
    return f"/api/v1/clients/{client.pk}/{action}/"


@pytest.fixture
def team() -> dict[str, Any]:
    client = make_client()
    return {"client": client, "admin_api": api_for("admin", client)}


def add(api: Any, client: Any, **body: Any) -> Any:
    return api.post(url(client, "add-member"), body, format="json")


def test_the_team_lists_everyone_with_their_role(team) -> None:
    api_for("viewer", team["client"])
    rows = team["admin_api"].get(url(team["client"], "members")).json()
    assert sorted(r["role"] for r in rows) == ["admin", "viewer"]
    assert sum(r["is_you"] for r in rows) == 1


def test_adding_an_existing_user_gives_them_access(team) -> None:
    other = make_user()
    response = add(team["admin_api"], team["client"], email=other.email, role="reviewer")
    assert response.status_code == 201, response.content
    assert response.json()["role"] == "reviewer"
    assert ClientMembership.objects.active().filter(client=team["client"], user=other).exists()
    # and they can now see the client
    assert as_user(other).get(f"/api/v1/clients/{team['client'].pk}/").status_code == 200


def test_a_new_person_gets_an_account_with_the_chosen_password(team) -> None:
    response = add(
        team["admin_api"],
        team["client"],
        email="New.Person@Example.com",
        role="manager",
        name="New Person",
        password=PASSWORD,
    )
    assert response.status_code == 201, response.content
    user = User.objects.get(email="new.person@example.com")
    assert user.check_password(PASSWORD)
    assert user.name == "New Person"


def test_a_new_person_needs_a_password_and_it_must_be_strong(team) -> None:
    api = team["admin_api"]
    assert add(api, team["client"], email="a@example.com", role="viewer").status_code == 400
    weak = add(api, team["client"], email="a@example.com", role="viewer", password="123")
    assert weak.status_code == 400
    assert not User.objects.filter(email="a@example.com").exists()


def test_changing_a_role_and_removing_access(team) -> None:
    other = make_user()
    api = team["admin_api"]
    add(api, team["client"], email=other.email, role="viewer")
    changed = api.post(
        url(team["client"], "set-member-role"),
        {"user_id": str(other.pk), "role": "manager"},
        format="json",
    )
    assert changed.json()["role"] == "manager"
    removed = api.post(
        url(team["client"], "remove-member"), {"user_id": str(other.pk)}, format="json"
    )
    assert removed.status_code == 204
    assert as_user(other).get(f"/api/v1/clients/{team['client'].pk}/").status_code == 404


def test_the_last_admin_cannot_be_removed_or_demoted(team) -> None:
    api = team["admin_api"]
    [row] = api.get(url(team["client"], "members")).json()
    removal = api.post(
        url(team["client"], "remove-member"), {"user_id": row["user_id"]}, format="json"
    )
    demotion = api.post(
        url(team["client"], "set-member-role"),
        {"user_id": row["user_id"], "role": "viewer"},
        format="json",
    )
    assert removal.status_code == 400
    assert demotion.status_code == 400
    assert "at least one admin" in str(removal.json())


def test_a_second_admin_makes_it_possible(team) -> None:
    other = make_user()
    api = team["admin_api"]
    add(api, team["client"], email=other.email, role="admin")
    [mine] = [r for r in api.get(url(team["client"], "members")).json() if r["is_you"]]
    response = api.post(
        url(team["client"], "remove-member"), {"user_id": mine["user_id"]}, format="json"
    )
    assert response.status_code == 204


def test_removing_someone_who_is_not_on_the_team_is_a_400(team) -> None:
    response = team["admin_api"].post(
        url(team["client"], "remove-member"), {"user_id": str(make_user().pk)}, format="json"
    )
    assert response.status_code == 400


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager"])
def test_only_admins_manage_the_team(team, role) -> None:
    api = api_for(role, team["client"])
    assert api.get(url(team["client"], "members")).status_code == 403
    response = add(api, team["client"], email="x@example.com", role="viewer", password=PASSWORD)
    assert response.status_code == 403
    assert not User.objects.filter(email="x@example.com").exists()


def test_another_clients_admin_cannot_see_or_change_the_team(team) -> None:
    stranger = api_for("admin", make_client())
    assert stranger.get(url(team["client"], "members")).status_code == 404
    assert add(stranger, team["client"], email="x@example.com", role="viewer").status_code == 404


# ------------------------------------------------------------------ password


def test_changing_your_own_password() -> None:
    user = make_user()
    user.set_password(PASSWORD)
    user.save()
    api = as_user(user)
    response = api.post(
        "/api/v1/auth/change-password/",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        format="json",
    )
    assert response.status_code == 204, response.content
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)


def test_the_current_password_is_required_and_the_new_one_must_be_strong() -> None:
    user = make_user()
    user.set_password(PASSWORD)
    user.save()
    api = as_user(user)
    wrong = api.post(
        "/api/v1/auth/change-password/",
        {"current_password": "nope", "new_password": NEW_PASSWORD},
        format="json",
    )
    weak = api.post(
        "/api/v1/auth/change-password/",
        {"current_password": PASSWORD, "new_password": "123"},
        format="json",
    )
    assert wrong.status_code == 400
    assert weak.status_code == 400
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_changing_a_password_needs_sign_in() -> None:
    from rest_framework.test import APIClient

    response = APIClient().post(
        "/api/v1/auth/change-password/",
        {"current_password": "a", "new_password": "b"},
        format="json",
    )
    assert response.status_code == 401
