"""CORS (issue #228, ADR 0010): the browser may call /api/ from the allowlisted frontend origin."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.http import HttpResponse
from rest_framework.test import APIClient

from tests.factories import make_client, make_user
from tests.test_campaign_api import URL as CAMPAIGNS_URL
from tests.test_campaign_api import api_for, create_body

pytestmark = [pytest.mark.api, pytest.mark.django_db]

ALLOWED = "http://localhost:3000"
OTHER = "https://evil.example.test"
CLIENTS_URL = "/api/v1/clients/"
ACAO = "Access-Control-Allow-Origin"


def preflight(
    api: APIClient,
    path: str,
    origin: str,
    method: str = "POST",
    headers: str = "authorization, content-type",
) -> HttpResponse:
    return api.options(
        path,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=headers,
    )


def cors_headers(response: HttpResponse) -> list[str]:
    return [name for name in response.headers if name.lower().startswith("access-control-")]


def test_allowed_origin_gets_cors_header_without_credentials(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/", HTTP_ORIGIN=ALLOWED)
    assert response[ACAO] == ALLOWED
    assert "origin" in response["Vary"].lower()  # caches must not share it across origins
    assert "Access-Control-Allow-Credentials" not in response


def test_disallowed_origin_gets_no_cors_headers(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/", HTTP_ORIGIN=OTHER)
    assert response.status_code == 200  # CORS is enforced by the browser; the API still answers
    assert cors_headers(response) == []


def test_wildcard_and_credentials_are_off(api_client: APIClient) -> None:
    assert "*" not in settings.CORS_ALLOWED_ORIGINS
    assert settings.CORS_ALLOW_CREDENTIALS is False
    assert api_client.get("/api/v1/", HTTP_ORIGIN=ALLOWED)[ACAO] != "*"


def test_preflight_from_allowed_origin(api_client: APIClient) -> None:
    response = preflight(api_client, CLIENTS_URL, ALLOWED, "PATCH")
    assert response.status_code == 200
    assert response[ACAO] == ALLOWED
    methods = {m.strip() for m in response["Access-Control-Allow-Methods"].split(",")}
    assert {"GET", "POST", "PUT", "PATCH", "OPTIONS"} <= methods
    assert "DELETE" not in methods
    allowed_headers = {h.strip() for h in response["Access-Control-Allow-Headers"].split(",")}
    assert {"authorization", "content-type", "accept", "x-request-id"} <= allowed_headers
    assert response["Access-Control-Max-Age"] == str(settings.CORS_PREFLIGHT_MAX_AGE)
    assert "Access-Control-Allow-Credentials" not in response
    assert response["X-Request-ID"]  # the request-ID middleware still wraps the preflight


def test_preflight_from_disallowed_origin_gets_no_permission(api_client: APIClient) -> None:
    response = preflight(api_client, CLIENTS_URL, OTHER)
    assert cors_headers(response) == []


def test_preflight_does_not_allow_unlisted_request_headers(api_client: APIClient) -> None:
    response = preflight(api_client, CLIENTS_URL, ALLOWED, headers="authorization, x-evil")
    assert "x-evil" not in response.get("Access-Control-Allow-Headers", "").lower()


def test_exposed_headers_on_a_real_api_response() -> None:
    client = make_client()
    api = APIClient()
    api.force_authenticate(make_user(is_superuser=True))
    response = api.get(f"{CLIENTS_URL}{client.pk}/", HTTP_ORIGIN=ALLOWED)
    assert response.status_code == 200
    assert response[ACAO] == ALLOWED
    exposed = {h.strip() for h in response["Access-Control-Expose-Headers"].split(",")}
    assert {"X-Profile-Version-Created", "X-Request-ID", "Retry-After"} <= exposed
    assert response["X-Request-ID"]


def test_error_responses_carry_cors_headers_too(api_client: APIClient) -> None:
    # A 401 must stay readable by the frontend, or it could not refresh the access token.
    response = api_client.get(CLIENTS_URL, HTTP_ORIGIN=ALLOWED)
    assert response.status_code == 401
    assert response[ACAO] == ALLOWED


def test_campaign_create_exposes_profile_version_header() -> None:
    client = make_client()
    response = api_for("manager", client).post(
        CAMPAIGNS_URL, create_body(client), format="json", HTTP_ORIGIN=ALLOWED
    )
    assert response.status_code == 201, response.content
    assert response["X-Profile-Version-Created"] == "true"
    assert "X-Profile-Version-Created" in response["Access-Control-Expose-Headers"]


@pytest.mark.parametrize("path", ["/admin/login/", "/healthz", "/readyz"])
def test_admin_and_probes_are_unaffected(api_client: APIClient, path: str) -> None:
    assert cors_headers(api_client.get(path, HTTP_ORIGIN=ALLOWED)) == []


def test_admin_preflight_gets_no_cors_headers(api_client: APIClient) -> None:
    assert cors_headers(preflight(api_client, "/admin/login/", ALLOWED)) == []


def test_middleware_order() -> None:
    mw = settings.MIDDLEWARE
    cors = mw.index("corsheaders.middleware.CorsMiddleware")
    assert mw.index("apps.core.middleware.RequestIDMiddleware") < cors
    assert cors < mw.index("django.middleware.security.SecurityMiddleware")
    assert cors < mw.index("django.middleware.common.CommonMiddleware")
