"""Auth endpoints: login, refresh (rotation + blacklist), logout, me, throttling, log hygiene."""

from __future__ import annotations

import io
import logging
from datetime import timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import User
from apps.core.logging import JsonFormatter, RedactionFilter, RequestContextFilter
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = [pytest.mark.api, pytest.mark.django_db]

LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
GENERIC_LOGIN_ERROR = "No active account found with the given credentials"


def error_of(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.content
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response["X-Request-ID"]
    return dict(error)


def login(client: APIClient, email: str, password: str = DEFAULT_PASSWORD) -> Any:
    return client.post(LOGIN, {"email": email, "password": password}, format="json")


# ----------------------------------------------------------------------------- login
def test_login_returns_tokens_and_me_works(api_client: APIClient, user: User):
    response = login(api_client, user.email)
    assert response.status_code == 200
    tokens = response.json()
    assert set(tokens) == {"access", "refresh"}
    assert "Set-Cookie" not in response.headers  # cookie delivery is off by default

    me = api_client.get(ME, headers={"Authorization": f"Bearer {tokens['access']}"})
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == str(user.id)
    assert body["email"] == user.email
    assert body["name"] == user.name
    assert "password" not in body


def test_login_email_is_case_insensitive_and_updates_last_login(api_client: APIClient):
    user = make_user(email="Mixed.Case@Example.com")
    assert user.email == "mixed.case@example.com"
    assert login(api_client, "MIXED.case@EXAMPLE.com").status_code == 200
    user.refresh_from_db()
    assert user.last_login is not None


def test_access_token_lifetime_is_fifteen_minutes(user: User):
    token = AccessToken.for_user(user)
    assert token.lifetime == timedelta(minutes=15)
    refresh = RefreshToken.for_user(user)
    assert refresh.lifetime == timedelta(days=7)


def test_login_wrong_password_is_generic(api_client: APIClient, user: User):
    response = login(api_client, user.email, "definitely-wrong-password")
    error = error_of(response, 401, "authentication_failed")
    assert error["message"] == GENERIC_LOGIN_ERROR


def test_login_unknown_email_looks_identical_to_wrong_password(api_client: APIClient, user: User):
    wrong_password = login(api_client, user.email, "definitely-wrong-password")
    unknown = login(api_client, "nobody@example.com")
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["error"]["message"] == wrong_password.json()["error"]["message"]
    assert unknown.json()["error"]["code"] == wrong_password.json()["error"]["code"]


def test_login_inactive_user_is_rejected_generically(api_client: APIClient):
    inactive = make_user(is_active=False)
    error = error_of(login(api_client, inactive.email), 401, "authentication_failed")
    assert error["message"] == GENERIC_LOGIN_ERROR


def test_login_unusable_password_is_rejected(api_client: APIClient):
    invited = User.objects.create_user(email="invited@example.com")  # no password set yet
    assert not invited.has_usable_password()
    assert login(api_client, invited.email, "").status_code == 400
    assert login(api_client, invited.email, "anything-at-all").status_code == 401


def test_login_missing_fields_is_a_validation_error(api_client: APIClient):
    response = api_client.post(LOGIN, {}, format="json")
    error = error_of(response, 400, "validation_error")
    assert set(error["details"]) == {"email", "password"}


def test_login_ignores_a_bad_authorization_header(api_client: APIClient, user: User):
    response = api_client.post(
        LOGIN,
        {"email": user.email, "password": DEFAULT_PASSWORD},
        format="json",
        headers={"Authorization": "Bearer garbage"},
    )
    assert response.status_code == 200


def test_login_is_post_only(api_client: APIClient):
    error_of(api_client.get(LOGIN), 405, "method_not_allowed")


# ----------------------------------------------------------------------------- throttling
def test_login_is_rate_limited_per_email(api_client: APIClient, user: User):
    for _ in range(5):  # default login_email rate: 5/min
        assert login(api_client, user.email, "wrong-password-x").status_code == 401
    # Locked out even with the right password until the window passes.
    error = error_of(login(api_client, user.email), 429, "throttled")
    assert error["details"]["retry_after"] > 0
    # A different account from the same IP is not affected by the per-email counter.
    other = make_user()
    assert login(api_client, other.email).status_code == 200


def test_login_throttle_email_key_is_case_insensitive(api_client: APIClient, user: User):
    for i in range(5):
        email = user.email.upper() if i % 2 else user.email
        assert login(api_client, email, "wrong-password-x").status_code == 401
    error_of(login(api_client, user.email), 429, "throttled")


def test_login_is_rate_limited_per_ip(api_client: APIClient, settings):
    rest = dict(settings.REST_FRAMEWORK)
    rest["DEFAULT_THROTTLE_RATES"] = {**rest["DEFAULT_THROTTLE_RATES"], "login": "3/min"}
    settings.REST_FRAMEWORK = rest
    for i in range(3):
        assert login(api_client, f"nobody{i}@example.com").status_code == 401
    error_of(login(api_client, "nobody99@example.com"), 429, "throttled")


# ----------------------------------------------------------------------------- me / access tokens
def test_me_requires_authentication(api_client: APIClient):
    response = api_client.get(ME)
    error_of(response, 401, "not_authenticated")
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_me_with_auth_client_fixture(auth_client: APIClient, user: User):
    assert auth_client.get(ME).json()["email"] == user.email


@pytest.mark.parametrize(
    "header",
    ["Bearer", "Bearer a b", "Bearer not-a-jwt", "Token abc", "Basic Zm9vOmJhcg=="],
)
def test_malformed_authorization_header(api_client: APIClient, header: str):
    response = api_client.get(ME, headers={"Authorization": header})
    # "Token ..." and "Basic ..." are not our scheme: treated as no credentials at all.
    code = (
        "not_authenticated" if header.split()[0] in {"Token", "Basic"} else "authentication_failed"
    )
    error_of(response, 401, code)


def test_expired_access_token(api_client: APIClient, user: User):
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=-timedelta(seconds=10))
    response = api_client.get(ME, headers={"Authorization": f"Bearer {token}"})
    error_of(response, 401, "authentication_failed")


def test_refresh_token_is_not_accepted_as_access_token(api_client: APIClient, user: User):
    refresh = RefreshToken.for_user(user)
    response = api_client.get(ME, headers={"Authorization": f"Bearer {refresh}"})
    error_of(response, 401, "authentication_failed")


def test_access_token_of_deactivated_user_is_rejected(api_client: APIClient, user: User):
    token = AccessToken.for_user(user)
    user.is_active = False
    user.save()
    response = api_client.get(ME, headers={"Authorization": f"Bearer {token}"})
    error_of(response, 401, "authentication_failed")


def test_session_login_does_not_authenticate_the_api(api_client: APIClient, user: User):
    api_client.force_login(user)  # a Django session cookie, as the admin uses
    error_of(api_client.get(ME), 401, "not_authenticated")


# ----------------------------------------------------------------------------- refresh
def test_refresh_rotates_and_blacklists_the_old_token(api_client: APIClient, user: User):
    first = login(api_client, user.email).json()
    response = api_client.post(REFRESH, {"refresh": first["refresh"]}, format="json")
    assert response.status_code == 200
    second = response.json()
    assert set(second) == {"access", "refresh"}
    assert second["refresh"] != first["refresh"]
    assert BlacklistedToken.objects.count() == 1
    assert OutstandingToken.objects.count() == 2
    # The new access token works.
    me = api_client.get(ME, headers={"Authorization": f"Bearer {second['access']}"})
    assert me.status_code == 200


def test_reused_refresh_token_is_rejected(api_client: APIClient, user: User):
    first = login(api_client, user.email).json()
    assert api_client.post(REFRESH, {"refresh": first["refresh"]}, format="json").status_code == 200
    reuse = api_client.post(REFRESH, {"refresh": first["refresh"]}, format="json")
    error_of(reuse, 401, "authentication_failed")


def test_expired_refresh_token_is_rejected(api_client: APIClient, user: User):
    token = RefreshToken.for_user(user)
    token.set_exp(lifetime=-timedelta(seconds=10))
    error_of(
        api_client.post(REFRESH, {"refresh": str(token)}, format="json"),
        401,
        "authentication_failed",
    )


def test_malformed_refresh_token_is_rejected(api_client: APIClient):
    error_of(
        api_client.post(REFRESH, {"refresh": "nope"}, format="json"), 401, "authentication_failed"
    )


def test_access_token_is_not_accepted_for_refresh(api_client: APIClient, user: User):
    access = AccessToken.for_user(user)
    error_of(
        api_client.post(REFRESH, {"refresh": str(access)}, format="json"),
        401,
        "authentication_failed",
    )


def test_missing_refresh_token_is_a_validation_error(api_client: APIClient):
    error = error_of(api_client.post(REFRESH, {}, format="json"), 400, "validation_error")
    assert "refresh" in error["details"]


def test_refresh_for_deactivated_user_is_rejected(api_client: APIClient, user: User):
    tokens = login(api_client, user.email).json()
    user.is_active = False
    user.save()
    response = api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    error_of(response, 401, "authentication_failed")


def test_refresh_for_deleted_user_is_401_not_500(api_client: APIClient, user: User):
    token = str(RefreshToken.for_user(user))
    OutstandingToken.objects.all().delete()  # let the user be deleted without FK cascade surprises
    user.delete()
    error_of(
        api_client.post(REFRESH, {"refresh": token}, format="json"), 401, "authentication_failed"
    )


def test_refresh_ignores_a_stale_access_header(api_client: APIClient, user: User):
    tokens = login(api_client, user.email).json()
    response = api_client.post(
        REFRESH,
        {"refresh": tokens["refresh"]},
        format="json",
        headers={"Authorization": "Bearer expired.or.garbage"},
    )
    assert response.status_code == 200


# ----------------------------------------------------------------------------- logout
def test_logout_blacklists_the_refresh_token(api_client: APIClient, user: User):
    tokens = login(api_client, user.email).json()
    response = api_client.post(LOGOUT, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 204
    assert BlacklistedToken.objects.count() == 1
    error_of(
        api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json"),
        401,
        "authentication_failed",
    )


def test_logout_twice_is_rejected_the_second_time(api_client: APIClient, user: User):
    tokens = login(api_client, user.email).json()
    assert api_client.post(LOGOUT, {"refresh": tokens["refresh"]}, format="json").status_code == 204
    error_of(
        api_client.post(LOGOUT, {"refresh": tokens["refresh"]}, format="json"),
        401,
        "authentication_failed",
    )


def test_logout_with_bad_or_missing_token(api_client: APIClient):
    error_of(
        api_client.post(LOGOUT, {"refresh": "garbage"}, format="json"), 401, "authentication_failed"
    )
    error_of(api_client.post(LOGOUT, {}, format="json"), 400, "validation_error")


def test_logout_works_when_the_access_token_already_expired(api_client: APIClient, user: User):
    tokens = login(api_client, user.email).json()
    expired = AccessToken.for_user(user)
    expired.set_exp(lifetime=-timedelta(seconds=10))
    response = api_client.post(
        LOGOUT,
        {"refresh": tokens["refresh"]},
        format="json",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 204


def test_logout_does_not_revoke_other_sessions(api_client: APIClient, user: User):
    one = login(api_client, user.email).json()
    two = login(api_client, user.email).json()
    assert api_client.post(LOGOUT, {"refresh": one["refresh"]}, format="json").status_code == 204
    assert api_client.post(REFRESH, {"refresh": two["refresh"]}, format="json").status_code == 200


# ----------------------------------------------------------------------------- log hygiene
def test_auth_flow_never_logs_passwords_or_tokens(
    api_client: APIClient, user: User, caplog: pytest.LogCaptureFixture
):
    """Run the whole flow with every log level on; no secret may appear in any log output.

    ``caplog`` sees the raw records (proves our code never logs secrets); the second handler
    mirrors the production redaction pipeline and is checked as well.
    """
    secret_password = "Wr0ng-Passw0rd-Marker-9917"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        login(api_client, user.email, secret_password)  # failure path
        tokens = login(api_client, user.email).json()  # success path
        api_client.get(ME, headers={"Authorization": f"Bearer {tokens['access']}"})
        api_client.get(ME, headers={"Authorization": "Bearer bad.token.value"})
        rotated = api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").json()
        api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")  # reuse
        api_client.post(LOGOUT, {"refresh": rotated["refresh"]}, format="json")
    finally:
        root.removeHandler(handler)

    secrets = [
        secret_password,
        DEFAULT_PASSWORD,
        tokens["access"],
        tokens["refresh"],
        rotated["access"],
        rotated["refresh"],
        "bad.token.value",
    ]
    raw = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert caplog.records, "expected some log output from the flow"
    for secret in secrets:
        assert secret not in raw
        assert secret not in stream.getvalue()


def test_error_responses_do_not_echo_credentials(api_client: APIClient, user: User):
    response = login(api_client, user.email, "echo-check-password-77")
    assert "echo-check-password-77" not in response.content.decode()
