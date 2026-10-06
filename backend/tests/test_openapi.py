"""OpenAPI schema (drf-spectacular): generation, committed snapshot, docs endpoints.

``test_committed_schema_is_up_to_date`` is the CI gate: change a serializer or view without
regenerating ``docs/api/openapi.yaml`` (``make api-schema``) and it fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED = REPO_ROOT / "docs" / "api" / "openapi.yaml"
REGENERATE = "run `make api-schema` and commit docs/api/openapi.yaml"

pytestmark = pytest.mark.skipif(
    not COMMITTED.parent.exists(), reason="repository docs are not available (e.g. in the image)"
)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> str:
    """The schema as the `spectacular` command writes it; fails on any schema warning."""
    out = tmp_path_factory.mktemp("schema") / "openapi.yaml"
    call_command("spectacular", file=str(out), validate=True, fail_on_warn=True)
    return out.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def schema(generated: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(generated)
    return loaded


def operations(schema: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """``operationId`` -> (method, path, operation)."""
    found = {}
    for path, item in schema["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                found[item[method]["operationId"]] = (method, path, item[method])
    return found


def test_committed_schema_is_up_to_date(generated: str) -> None:
    assert COMMITTED.read_text(encoding="utf-8") == generated, (
        f"docs/api/openapi.yaml is out of date: {REGENERATE}"
    )


def test_operations_have_stable_ids_and_tags(schema: dict[str, Any]) -> None:
    ops = operations(schema)
    assert {op_id: (m.upper(), p) for op_id, (m, p, _) in ops.items()} == {
        "api_root": ("GET", "/api/v1/"),
        "auth_login": ("POST", "/api/v1/auth/login/"),
        "auth_refresh": ("POST", "/api/v1/auth/refresh/"),
        "auth_logout": ("POST", "/api/v1/auth/logout/"),
        "auth_me": ("GET", "/api/v1/auth/me/"),
        "health_live": ("GET", "/healthz"),
        "health_ready": ("GET", "/readyz"),
    }
    for op_id, (_, path, op) in ops.items():
        expected_tag = (
            "auth" if "/auth/" in path else "health" if op_id.startswith("health") else "meta"
        )
        assert op["tags"] == [expected_tag], op_id
    assert {t["name"] for t in schema["tags"]} == {"auth", "health", "meta"}


def test_jwt_bearer_security_scheme(schema: dict[str, Any]) -> None:
    scheme = schema["components"]["securitySchemes"]["jwtAuth"]
    assert (scheme["type"], scheme["scheme"], scheme["bearerFormat"]) == ("http", "bearer", "JWT")
    ops = operations(schema)
    assert ops["auth_me"][2]["security"] == [{"jwtAuth": []}]
    for op_id in ("auth_login", "auth_refresh", "auth_logout", "health_live", "health_ready"):
        assert ops[op_id][2]["security"] == [{}], f"{op_id} must be public"


def test_error_envelope_is_a_component_used_by_every_operation(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    envelope = components["ErrorEnvelope"]
    assert envelope["required"] == ["error"]
    assert envelope["properties"]["error"] == {"$ref": "#/components/schemas/ErrorBody"}
    body = components["ErrorBody"]
    assert set(body["properties"]) == {"code", "message", "details", "request_id"}
    assert set(body["required"]) == {"code", "message", "details", "request_id"}

    ref = {"$ref": "#/components/schemas/ErrorEnvelope"}
    for op_id, (_, _, op) in operations(schema).items():
        errors = [
            status
            for status, response in op["responses"].items()
            if response.get("content", {}).get("application/json", {}).get("schema") == ref
        ]
        assert "500" in errors, f"{op_id}: no error envelope documented"
    login = operations(schema)["auth_login"][2]["responses"]
    assert {"400", "401", "429"} <= set(login)


# ---------------------------------------------------------------- HTTP endpoints


@pytest.fixture
def docs_on() -> Any:
    with override_settings(API_DOCS_ENABLED=True):
        yield


@pytest.mark.usefixtures("docs_on")
def test_schema_endpoint_serves_the_committed_schema(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/schema/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.oai.openapi")
    served = yaml.safe_load(response.content)
    committed = yaml.safe_load(COMMITTED.read_text(encoding="utf-8"))
    # `info.version` gets the request's API version appended when served, so compare the rest.
    assert served["paths"] == committed["paths"]
    assert served["components"] == committed["components"]


@pytest.mark.usefixtures("docs_on")
@pytest.mark.parametrize("path", ["/api/v1/docs/", "/api/v1/redoc/"])
def test_docs_pages_render(api_client: APIClient, path: str) -> None:
    response = api_client.get(path)
    assert response.status_code == 200
    assert b"/api/v1/schema/" in response.content


@pytest.mark.parametrize("path", ["/api/v1/schema/", "/api/v1/docs/", "/api/v1/redoc/"])
def test_docs_are_disabled_by_default(api_client: APIClient, path: str) -> None:
    response = api_client.get(path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
