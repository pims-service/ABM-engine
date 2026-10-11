"""Connection test action (issue #77): ``POST /api/v1/integrations/connections/{id}/test/`` and the
tester registry in ``apps.integrations.testing``. Honest results, no secrets anywhere, a rate
limit, an audit entry, admin only."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Client
from apps.core.models import AuditLog
from apps.integrations import testing
from apps.integrations.api.throttles import ConnectionTestThrottle
from apps.integrations.crypto import Credentials
from apps.integrations.models import ConnectionStatus, IntegrationConnection
from tests.factories import make_client, make_user
from tests.factories_integrations import FAKE_SECRET, make_connection

pytestmark = [pytest.mark.api, pytest.mark.django_db]

URL = "/api/v1/integrations/connections/"


def test_url(connection: IntegrationConnection) -> str:
    return f"{URL}{connection.pk}/test/"


test_url.__test__ = False  # type: ignore[attr-defined]


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def member(role: str, client: Client) -> User:
    user = make_user()
    grant_membership(client, user, role)
    return user


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def run(connection: IntegrationConnection, user: User) -> Any:
    return as_user(user).post(test_url(connection), format="json")


# ------------------------------------------------------------------ results


def test_fake_provider_succeeds_and_stores_the_result() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client)
    response = run(connection, admin)
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["result"] == "ok"
    assert data["ok"] is True
    assert data["message"] == "Fake provider: connection OK."
    assert data["connection"]["last_test_status"] == "ok"
    assert data["connection"]["last_tested_at"] is not None
    connection.refresh_from_db()
    assert connection.last_test_status == "ok"
    assert connection.last_test_message == "Fake provider: connection OK."


@pytest.mark.parametrize(
    ("simulate", "fragment"),
    [
        ("auth_failed", "Authentication failed"),
        ("rate_limited", "Rate limited"),
        ("error", "error"),
    ],
)
def test_simulated_failures_are_readable_and_flag_the_connection(
    simulate: str, fragment: str
) -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, config={"simulate": simulate})
    data = run(connection, admin).json()
    assert data["result"] == "failed"
    assert data["ok"] is False
    assert fragment in data["message"]
    assert data["connection"]["status"] == "error"
    assert data["connection"]["last_test_status"] == "failed"


def test_a_later_success_clears_the_error_state() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, config={"simulate": "auth_failed"})
    run(connection, admin)
    api = as_user(admin)
    response = api.patch(f"{URL}{connection.pk}/", {"config": {"model": "fake-1"}}, format="json")
    assert response.status_code == 200
    data = run(connection, admin).json()
    assert data["result"] == "ok"
    assert data["connection"]["status"] == "active"


def test_a_provider_without_a_tester_is_unconfigured_not_ok() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, provider="not_built_yet")
    data = run(connection, admin).json()
    assert data["result"] == "unconfigured"
    assert data["ok"] is False
    assert data["message"] == "No test available for this provider yet."
    connection.refresh_from_db()
    assert connection.last_test_status == ""  # nothing was tested, nothing is claimed
    assert connection.last_tested_at is None
    assert connection.status == ConnectionStatus.ACTIVE


def test_unconfigured_does_not_need_credentials() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, provider="not_built_yet", credentials=None)
    assert run(connection, admin).json()["result"] == "unconfigured"


def test_no_credentials_fails_honestly_for_a_provider_with_a_tester() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, credentials=None)
    data = run(connection, admin).json()
    assert data["result"] == "failed"
    assert "No credentials" in data["message"]


def test_a_disabled_connection_can_be_tested_and_stays_disabled() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, config={"simulate": "error"}, status="disabled")
    data = run(connection, admin).json()
    assert data["result"] == "failed"
    assert data["connection"]["status"] == "disabled"


def test_an_archived_connection_cannot_be_tested() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client)
    api = as_user(admin)
    assert api.post(f"{URL}{connection.pk}/archive/").status_code == 200
    response = run(connection, admin)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_a_crashing_tester_gives_a_generic_failure_without_its_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
        raise RuntimeError(f"request with key {credentials.get('api_key')} failed")

    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, provider="crashy")
    testing.register_tester("llm", "crashy", boom)
    try:
        with caplog.at_level(logging.DEBUG):
            response = run(connection, admin)
    finally:
        testing.unregister_tester("llm", "crashy")
    data = response.json()
    assert data["result"] == "failed"
    assert data["message"] == testing.TEST_CRASHED_MESSAGE
    assert FAKE_SECRET not in response.content.decode()
    assert FAKE_SECRET not in caplog.text


def test_a_tester_message_is_scrubbed_before_it_is_stored_or_returned() -> None:
    def leaky(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
        return False, f"401 from provider, api_key={credentials.get('api_key')}"

    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, provider="leaky")
    testing.register_tester("llm", "leaky", leaky)
    try:
        response = run(connection, admin)
    finally:
        testing.unregister_tester("llm", "leaky")
    assert FAKE_SECRET not in response.content.decode()
    connection.refresh_from_db()
    assert FAKE_SECRET not in connection.last_test_message
    assert "401 from provider" in connection.last_test_message


def test_a_registered_tester_gets_the_credentials_and_the_connection() -> None:
    seen: dict[str, Any] = {}

    def tester(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
        seen["key"] = credentials.get("api_key")
        seen["pk"] = connection.pk
        return True, "Provider reachable."

    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, category="crm", provider="mycrm")
    testing.register_tester("crm", "mycrm", tester)
    try:
        data = run(connection, admin).json()
    finally:
        testing.unregister_tester("crm", "mycrm")
    assert data["result"] == "ok"
    assert seen == {"key": FAKE_SECRET, "pk": connection.pk}


# ------------------------------------------------------------------ registry


def test_register_tester_rejects_unknown_categories_and_duplicates() -> None:
    with pytest.raises(ValueError, match="Unknown integration category"):
        testing.register_tester("nope", "x", lambda c, k: (True, ""))
    with pytest.raises(ValueError, match="already registered"):
        testing.register_tester("llm", "fake", lambda c, k: (True, ""))
    assert testing.get_tester("llm", "fake") is not None
    assert testing.get_tester("llm", "missing") is None


# ------------------------------------------------------------------ secrets, audit, logs


def test_the_secret_is_never_returned_logged_or_audited(caplog: pytest.LogCaptureFixture) -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client)
    with caplog.at_level(logging.DEBUG):
        response = run(connection, admin)
    assert FAKE_SECRET not in response.content.decode()
    assert "credentials" not in response.json()["connection"]
    assert FAKE_SECRET not in caplog.text
    for entry in AuditLog.objects.all():
        assert FAKE_SECRET not in str(entry.before) + str(entry.after)


def test_a_test_is_audited_without_the_message() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client, config={"simulate": "auth_failed"})
    run(connection, admin)
    entry = AuditLog.objects.filter(object_type="integration_connection").latest("created_at")
    assert entry.actor_id == admin.pk
    assert entry.client_id == client.pk
    after = entry.after or {}
    assert after["connection_test"] == "failed"
    assert after["status"] == "error"
    assert "Authentication failed" not in str(after)


# ------------------------------------------------------------------ roles


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager"])
def test_only_admins_can_test(role: str) -> None:
    client = make_client()
    user = member(role, client)
    connection = make_connection(client)
    response = run(connection, user)
    assert response.status_code == 403
    connection.refresh_from_db()
    assert connection.last_test_status == ""


def test_a_stranger_gets_404_and_anonymous_401() -> None:
    connection = make_connection(make_client())
    outsider = member("admin", make_client())
    assert run(connection, outsider).status_code == 404
    assert APIClient().post(test_url(connection)).status_code == 401


def test_a_global_admin_can_test() -> None:
    connection = make_connection(make_client())
    superuser = make_user(is_superuser=True, is_staff=True)
    assert run(connection, superuser).status_code == 200


def test_get_is_not_allowed() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client)
    assert as_user(admin).get(test_url(connection)).status_code == 405


# ------------------------------------------------------------------ rate limit


def test_the_sixth_test_within_a_minute_is_throttled() -> None:
    client = make_client()
    admin = member("admin", client)
    connection = make_connection(client)
    for _ in range(5):
        assert run(connection, admin).status_code == 200
    response = run(connection, admin)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "throttled"
    assert "Retry-After" in response
    # Another connection has its own budget.
    other = make_connection(client)
    assert run(other, admin).status_code == 200


def test_the_limit_is_per_connection_not_per_user() -> None:
    client = make_client()
    first, second = member("admin", client), member("admin", client)
    connection = make_connection(client)
    for _ in range(5):
        assert run(connection, first).status_code == 200
    assert run(connection, second).status_code == 429


def test_a_user_without_the_role_cannot_use_up_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ConnectionTestThrottle, "rate", "2/min")
    client = make_client()
    admin, manager = member("admin", client), member("manager", client)
    connection = make_connection(client)
    for _ in range(5):
        assert run(connection, manager).status_code == 403
    assert run(connection, admin).status_code == 200
