"""Refresh token delivered as an httpOnly cookie (AUTH_REFRESH_COOKIE_ENABLED), for the BFF."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from tests.factories import DEFAULT_PASSWORD

pytestmark = [pytest.mark.api, pytest.mark.django_db]

LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
COOKIE = "abm_refresh"


@pytest.fixture
def cookie_mode(settings) -> None:
    settings.AUTH_REFRESH_COOKIE_ENABLED = True


def login(client: APIClient, user: User) -> Any:
    return client.post(LOGIN, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json")


def test_login_sets_httponly_cookie_and_hides_refresh_from_body(
    cookie_mode, api_client: APIClient, user: User
):
    response = login(api_client, user)
    assert response.status_code == 200
    assert set(response.json()) == {"access"}
    cookie = response.cookies[COOKIE]
    assert cookie.value
    assert cookie["httponly"]
    assert cookie["secure"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["max-age"] == 7 * 24 * 3600


def test_cookie_flags_follow_settings(cookie_mode, settings, api_client: APIClient, user: User):
    settings.AUTH_REFRESH_COOKIE_SECURE = False
    settings.AUTH_REFRESH_COOKIE_SAMESITE = "Strict"
    settings.AUTH_REFRESH_COOKIE_NAME = "custom_refresh"
    cookie = login(api_client, user).cookies["custom_refresh"]
    assert not cookie["secure"]
    assert cookie["samesite"] == "Strict"
    assert cookie["httponly"]


def test_refresh_reads_cookie_and_rotates_it(cookie_mode, api_client: APIClient, user: User):
    old = login(api_client, user).cookies[COOKIE].value
    response = api_client.post(REFRESH, {}, format="json")  # the test client sends its cookie jar
    assert response.status_code == 200
    assert set(response.json()) == {"access"}
    new = response.cookies[COOKIE].value
    assert new
    assert new != old

    # The rotated-out cookie value is blacklisted: replaying it fails and the cookie is dropped.
    api_client.cookies[COOKIE] = old
    replay = api_client.post(REFRESH, {}, format="json")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "authentication_failed"
    assert replay.cookies[COOKIE]["max-age"] == 0


def test_body_refresh_token_wins_over_cookie(cookie_mode, api_client: APIClient, user: User):
    login(api_client, user)
    api_client.cookies[COOKIE] = "garbage-cookie-value"
    other = APIClient()
    body_token = (
        other.post(LOGIN, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json")
        .cookies[COOKIE]
        .value
    )
    response = api_client.post(REFRESH, {"refresh": body_token}, format="json")
    assert response.status_code == 200


def test_refresh_without_cookie_or_body_is_a_validation_error(cookie_mode, api_client: APIClient):
    response = api_client.post(REFRESH, {}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_logout_uses_cookie_blacklists_and_clears_it(
    cookie_mode, api_client: APIClient, user: User
):
    token = login(api_client, user).cookies[COOKIE].value
    response = api_client.post(LOGOUT, {}, format="json")
    assert response.status_code == 204
    assert response.cookies[COOKIE].value == ""
    assert response.cookies[COOKIE]["max-age"] == 0

    api_client.cookies[COOKIE] = token
    assert api_client.post(REFRESH, {}, format="json").status_code == 401


def test_access_token_still_works_in_cookie_mode(cookie_mode, api_client: APIClient, user: User):
    access = login(api_client, user).json()["access"]
    me = api_client.get(ME, headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200


def test_cookie_is_ignored_when_the_flag_is_off(api_client: APIClient, user: User):
    response = login(api_client, user)
    assert COOKIE not in response.cookies
    assert "refresh" in response.json()
    api_client.cookies[COOKIE] = response.json()["refresh"]
    assert api_client.post(REFRESH, {}, format="json").status_code == 400
    assert api_client.post(LOGOUT, {}, format="json").status_code == 400
