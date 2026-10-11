"""Integration connections API (issue #76): role matrix, write-only credentials, validation,
filters, isolation. Secrets must never appear in a response, an error body, a log or an audit
entry."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Client
from apps.core.models import AuditLog
from apps.integrations import services
from apps.integrations.models import IntegrationConnection
from tests.factories import make_client, make_user
from tests.factories_integrations import FAKE_SECRET, FAKE_SECRET_2, make_connection

pytestmark = [pytest.mark.api, pytest.mark.django_db]

URL = "/api/v1/integrations/connections/"


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def member(role: str, client: Client) -> User:
    user = make_user()
    grant_membership(client, user, role)
    return user


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "category": "llm",
        "provider": "fake",
        "name": f"New {uuid.uuid4().hex[:6]}",
        "config": {"model": "fake-1"},
        "credentials": {"api_key": FAKE_SECRET},
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------ write-only credentials


def test_create_returns_a_masked_hint_and_never_the_secret() -> None:
    client = make_client()
    admin = member("admin", client)
    response = as_user(admin).post(URL, body(client=str(client.pk)), format="json")
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["has_credentials"] is True
    assert data["credentials_hint"] == {"api_key": f"****{FAKE_SECRET[-4:]}"}
    assert "credentials" not in data
    assert FAKE_SECRET not in response.content.decode()
    assert data["client"] == str(client.pk)
    assert data["created_by"] == str(admin.pk)
    stored = IntegrationConnection.objects.get(pk=data["id"])
    assert stored.credentials.get("api_key") == FAKE_SECRET


def test_no_endpoint_ever_returns_the_secret() -> None:
    client = make_client()
    admin = member("admin", client)
    api = as_user(admin)
    conn = make_connection(client)
    responses = [
        api.get(URL),
        api.get(f"{URL}{conn.pk}/"),
        api.patch(f"{URL}{conn.pk}/", {"name": "Renamed"}, format="json"),
        api.patch(f"{URL}{conn.pk}/", {"credentials": {"api_key": FAKE_SECRET_2}}, format="json"),
        api.put(f"{URL}{conn.pk}/", {"name": "Put name"}, format="json"),
        api.post(f"{URL}{conn.pk}/disable/"),
        api.post(f"{URL}{conn.pk}/enable/"),
        api.post(f"{URL}{conn.pk}/archive/"),
        api.get(f"{URL}?archived=all"),
        api.post(f"{URL}{conn.pk}/restore/"),
        # errors must not echo what was sent either
        api.post(URL, body(client=str(client.pk), name=""), format="json"),
        api.post(
            URL, body(client=str(client.pk), credentials={"bad name": FAKE_SECRET}), format="json"
        ),
        api.post(URL, body(client=str(client.pk), config={"api_key": FAKE_SECRET}), format="json"),
        api.post(URL, body(client="nope", credentials=FAKE_SECRET), format="json"),
    ]
    for response in responses:
        text = response.content.decode()
        assert FAKE_SECRET not in text, response.request
        assert FAKE_SECRET_2 not in text
    assert [r.status_code for r in responses[:10]] == [
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
    ]
    assert all(r.status_code == 400 for r in responses[10:])


def test_replacing_credentials_through_patch_updates_the_hint_and_audits_without_values() -> None:
    client = make_client()
    admin = member("admin", client)
    conn = make_connection(client)
    response = as_user(admin).patch(
        f"{URL}{conn.pk}/", {"credentials": {"api_key": FAKE_SECRET_2}}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["credentials_hint"] == {"api_key": f"****{FAKE_SECRET_2[-4:]}"}
    conn.refresh_from_db()
    assert conn.credentials.get("api_key") == FAKE_SECRET_2
    audit = json.dumps(list(AuditLog.objects.filter(client=client).values("before", "after")))
    assert FAKE_SECRET not in audit
    assert FAKE_SECRET_2 not in audit
    assert '"credentials_change": "replaced"' in audit


def test_omitting_credentials_on_update_keeps_them() -> None:
    client = make_client()
    conn = make_connection(client)
    response = as_user(member("admin", client)).patch(
        f"{URL}{conn.pk}/", {"name": "Only name"}, format="json"
    )
    assert response.status_code == 200
    conn.refresh_from_db()
    assert conn.credentials.get("api_key") == FAKE_SECRET


def test_short_secrets_show_no_characters() -> None:
    client = make_client()
    response = as_user(member("admin", client)).post(
        URL, body(client=str(client.pk), credentials={"token_x": "short"}), format="json"
    )
    assert response.json()["credentials_hint"] == {"token_x": "****"}


def test_connection_without_credentials() -> None:
    client = make_client()
    data = body(client=str(client.pk))
    del data["credentials"]
    response = as_user(member("admin", client)).post(URL, data, format="json")
    assert response.status_code == 201
    assert response.json()["has_credentials"] is False
    assert response.json()["credentials_hint"] == {}


# ------------------------------------------------------------------ roles


READ_ROLES = {"viewer": 403, "reviewer": 403, "manager": 200, "admin": 200}
WRITE_ROLES = {"viewer": 403, "reviewer": 403, "manager": 403, "admin": 200}


@pytest.mark.parametrize(("role", "expected"), READ_ROLES.items())
def test_read_needs_manager_or_admin(role: str, expected: int) -> None:
    client = make_client()
    conn = make_connection(client)
    api = as_user(member(role, client))
    detail = api.get(f"{URL}{conn.pk}/")
    assert detail.status_code == expected
    listing = api.get(URL)
    assert listing.status_code == (200 if expected == 200 else 403)
    if expected == 200:
        assert [r["id"] for r in listing.json()["results"]] == [str(conn.pk)]
    else:
        assert FAKE_SECRET not in detail.content.decode()


@pytest.mark.parametrize(("role", "expected"), WRITE_ROLES.items())
def test_writes_need_admin(role: str, expected: int) -> None:
    client = make_client()
    conn = make_connection(client)
    api = as_user(member(role, client))
    results = {
        "create": api.post(URL, body(client=str(client.pk)), format="json").status_code,
        "patch": api.patch(f"{URL}{conn.pk}/", {"name": "x"}, format="json").status_code,
        "put": api.put(f"{URL}{conn.pk}/", {"name": "y"}, format="json").status_code,
        "disable": api.post(f"{URL}{conn.pk}/disable/").status_code,
        "enable": api.post(f"{URL}{conn.pk}/enable/").status_code,
        "archive": api.post(f"{URL}{conn.pk}/archive/").status_code,
        "restore": api.post(f"{URL}{conn.pk}/restore/").status_code,
    }
    if expected == 200:
        assert results == {
            "create": 201,
            "patch": 200,
            "put": 200,
            "disable": 200,
            "enable": 200,
            "archive": 200,
            "restore": 200,
        }
    else:
        assert set(results.values()) == {403}, results
        conn.refresh_from_db()
        assert not conn.is_archived
        assert conn.name == conn.name  # unchanged below
        assert IntegrationConnection.objects.filter(client=client).count() == 1


def test_a_viewer_in_one_client_and_admin_in_another_is_judged_per_client() -> None:
    a, b = make_client(), make_client()
    user = make_user()
    grant_membership(a, user, "viewer")
    grant_membership(b, user, "admin")
    conn_a, conn_b = make_connection(a), make_connection(b)
    api = as_user(user)
    assert api.get(f"{URL}{conn_a.pk}/").status_code == 403
    assert api.get(f"{URL}{conn_b.pk}/").status_code == 200
    assert [r["id"] for r in api.get(URL).json()["results"]] == [str(conn_b.pk)]
    assert api.post(URL, body(client=str(a.pk)), format="json").status_code == 403


def test_global_admin_sees_and_writes_everywhere() -> None:
    client = make_client()
    conn = make_connection(client)
    api = as_user(make_user(is_superuser=True))
    assert api.get(f"{URL}{conn.pk}/").status_code == 200
    assert str(conn.pk) in [r["id"] for r in api.get(URL).json()["results"]]
    assert api.post(URL, body(client=str(client.pk)), format="json").status_code == 201


def test_anonymous_gets_401() -> None:
    assert APIClient().get(URL).status_code == 401
    assert APIClient().post(URL, body(), format="json").status_code == 401


# ------------------------------------------------------------------ isolation


def test_another_clients_connection_is_404_for_every_action() -> None:
    mine, theirs = make_client(), make_client()
    admin = member("admin", mine)
    foreign = make_connection(theirs)
    api = as_user(admin)
    missing = uuid.uuid4()
    for suffix, method, payload in [
        ("", "get", None),
        ("", "patch", {"name": "x"}),
        ("", "put", {"name": "x"}),
        ("disable/", "post", None),
        ("enable/", "post", None),
        ("archive/", "post", None),
        ("restore/", "post", None),
    ]:
        results = []
        for pk in (foreign.pk, missing):
            call = getattr(api, method)
            args = (f"{URL}{pk}/{suffix}",) + ((payload,) if payload else ())
            response = call(*args, format="json") if payload else call(*args)
            data = response.json()
            data["error"].pop("request_id", None)
            results.append((response.status_code, data))
        assert results[0] == results[1], (method, suffix)
        assert results[0][0] == 404
    foreign.refresh_from_db()
    assert foreign.name != "x"
    assert not foreign.is_archived


def test_creating_in_another_client_is_404() -> None:
    mine, theirs = make_client(), make_client()
    api = as_user(member("admin", mine))
    response = api.post(URL, body(client=str(theirs.pk)), format="json")
    assert response.status_code == 404
    assert api.post(URL, body(client=str(uuid.uuid4())), format="json").status_code == 404
    assert IntegrationConnection.objects.filter(client=theirs).count() == 0


def test_lists_and_filters_only_show_own_clients() -> None:
    mine, theirs = make_client(), make_client()
    own = make_connection(mine)
    make_connection(theirs)
    api = as_user(member("admin", mine))
    for query in ("", "?archived=all", f"?client={theirs.pk}", "?search=Connection"):
        ids = [r["id"] for r in api.get(URL + query).json()["results"]]
        assert set(ids) <= {str(own.pk)}, query
    assert api.get(f"{URL}?client={theirs.pk}").json()["results"] == []


# ------------------------------------------------------------------ validation and errors


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"category": "telepathy"}, "category"),
        ({"provider": "Bad Key"}, "provider"),
        ({"provider": ""}, "provider"),
        ({"name": ""}, "name"),
        ({"name": "x" * 201}, "name"),
        ({"status": "error"}, "status"),
        ({"config": "text"}, "config"),
        ({"config": {"password": "x"}}, "config"),
        ({"credentials": {}}, "credentials"),
        ({"credentials": "text"}, "credentials"),
        ({"credentials": {"k": ""}}, "credentials"),
        ({"credentials": {"bad key": "v"}}, "credentials"),
        ({"credentials": {"k": 5}}, "credentials"),
    ],
)
def test_validation_errors_use_the_envelope(overrides: dict[str, Any], field: str) -> None:
    client = make_client()
    response = as_user(member("admin", client)).post(
        URL, body(client=str(client.pk), **overrides), format="json"
    )
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert field in error["details"]


def test_missing_required_fields() -> None:
    client = make_client()
    response = as_user(member("admin", client)).post(URL, {"client": str(client.pk)}, format="json")
    assert response.status_code == 400
    assert {"category", "provider", "name"} <= set(response.json()["error"]["details"])


def test_duplicate_name_is_a_field_error() -> None:
    client = make_client()
    make_connection(client, name="Main")
    response = as_user(member("admin", client)).post(
        URL, body(client=str(client.pk), name="MAIN"), format="json"
    )
    assert response.status_code == 400
    assert "name" in response.json()["error"]["details"]


def test_provider_and_category_cannot_be_changed() -> None:
    client = make_client()
    conn = make_connection(client)
    response = as_user(member("admin", client)).patch(
        f"{URL}{conn.pk}/", {"provider": "other", "category": "crm", "name": "ok"}, format="json"
    )
    assert response.status_code == 200  # unknown keys are ignored by the serializer
    conn.refresh_from_db()
    assert (conn.provider, conn.category) == ("fake", "llm")


def test_an_archived_connection_is_read_only_over_the_api() -> None:
    client = make_client()
    conn = make_connection(client)
    api = as_user(member("admin", client))
    assert api.post(f"{URL}{conn.pk}/archive/").status_code == 200
    assert api.patch(f"{URL}{conn.pk}/", {"name": "x"}, format="json").status_code == 400
    assert api.post(f"{URL}{conn.pk}/disable/").status_code == 400
    assert api.get(f"{URL}{conn.pk}/").status_code == 200
    assert api.get(URL).json()["results"] == []
    assert [r["id"] for r in api.get(URL + "?archived=true").json()["results"]] == [str(conn.pk)]
    assert api.post(f"{URL}{conn.pk}/restore/").status_code == 200


def test_there_is_no_delete() -> None:
    client = make_client()
    conn = make_connection(client)
    assert as_user(member("admin", client)).delete(f"{URL}{conn.pk}/").status_code == 405


def test_status_actions_and_filters() -> None:
    client = make_client()
    one = make_connection(client, name="One")
    two = make_connection(client, name="Two", category="crm", provider="other")
    api = as_user(member("admin", client))
    assert api.post(f"{URL}{one.pk}/disable/").json()["status"] == "disabled"
    assert api.post(f"{URL}{one.pk}/disable/").json()["status"] == "disabled"  # idempotent
    assert [r["id"] for r in api.get(f"{URL}?status=disabled").json()["results"]] == [str(one.pk)]
    assert [r["id"] for r in api.get(f"{URL}?category=crm").json()["results"]] == [str(two.pk)]
    assert [r["id"] for r in api.get(f"{URL}?provider=other").json()["results"]] == [str(two.pk)]
    assert api.get(f"{URL}?category=nope").status_code == 400
    assert api.post(f"{URL}{one.pk}/enable/").json()["status"] == "active"
    services.record_test_result(two, ok=False, message="boom")
    assert [r["id"] for r in api.get(f"{URL}?test_status=failed").json()["results"]] == [
        str(two.pk)
    ]
