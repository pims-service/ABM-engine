from __future__ import annotations

import io
import json
import logging

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings
from rest_framework.test import APIClient

from apps.core.exceptions import GENERIC_SERVER_ERROR, error_payload, server_error_view
from apps.core.logging import JsonFormatter, RedactionFilter, RequestContextFilter

pytestmark = [pytest.mark.api, pytest.mark.django_db, pytest.mark.urls("tests.error_urls")]

ENVELOPE_KEYS = {"code", "message", "details", "request_id"}


def assert_envelope(response, status_code: int, code: str) -> dict:
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == ENVELOPE_KEYS
    assert error["code"] == code
    assert isinstance(error["message"], str)
    assert error["request_id"] == response["X-Request-ID"]
    return dict(error)


def test_validation_error_lists_field_errors(api_client: APIClient):
    response = api_client.post("/api/v1/validation/", {"age": -1}, format="json")
    error = assert_envelope(response, 400, "validation_error")
    assert error["message"] == "Request validation failed."
    assert error["details"]["name"] == ["This field is required."]
    assert error["details"]["age"] == ["Ensure this value is greater than or equal to 0."]


def test_parse_error(api_client: APIClient):
    response = api_client.post(
        "/api/v1/validation/", data="{not json", content_type="application/json"
    )
    error = assert_envelope(response, 400, "parse_error")
    assert error["details"] is None


def test_unsupported_media_type(api_client: APIClient):
    response = api_client.post("/api/v1/validation/", data="a=b", content_type="text/plain")
    assert_envelope(response, 415, "unsupported_media_type")


def test_authentication_failed_has_challenge_header(api_client: APIClient):
    response = api_client.get(
        "/api/v1/basic-auth/", headers={"Authorization": "Basic Zm9vOmJhcg=="}
    )
    assert_envelope(response, 401, "authentication_failed")
    assert "WWW-Authenticate" in response


def test_not_authenticated_basic(api_client: APIClient):
    response = api_client.get("/api/v1/basic-auth/")
    assert_envelope(response, 401, "not_authenticated")
    assert response["WWW-Authenticate"].startswith("Basic")


def test_not_authenticated_with_project_defaults(api_client: APIClient):
    response = api_client.get("/api/v1/session-auth/")
    assert_envelope(response, 403, "not_authenticated")


def test_permission_denied(api_client: APIClient):
    error = assert_envelope(api_client.get("/api/v1/forbidden/"), 403, "permission_denied")
    assert error["message"] == "You may not do that."


def test_django_permission_denied(api_client: APIClient):
    error = assert_envelope(api_client.get("/api/v1/django-forbidden/"), 403, "permission_denied")
    assert error["message"] == "Permission denied."


def test_not_found(api_client: APIClient):
    error = assert_envelope(api_client.get("/api/v1/not-found/"), 404, "not_found")
    assert error["message"] == "No such company."


def test_django_http404(api_client: APIClient):
    error = assert_envelope(api_client.get("/api/v1/django-404/"), 404, "not_found")
    assert error["message"] == "Not found."


def test_unknown_api_route_uses_envelope(api_client: APIClient):
    response = api_client.get("/api/v1/does-not-exist/")
    assert_envelope(response, 404, "not_found")


def test_unknown_non_api_route_is_not_json(api_client: APIClient):
    response = api_client.get("/somewhere/else/")
    assert response.status_code == 404
    assert b'"error"' not in response.content


def test_method_not_allowed(api_client: APIClient):
    response = api_client.get("/api/v1/only-post/")
    assert_envelope(response, 405, "method_not_allowed")
    assert response["Allow"]


def test_throttled_reports_retry_after(api_client: APIClient):
    response = api_client.get("/api/v1/throttled/")
    error = assert_envelope(response, 429, "throttled")
    assert error["details"] == {"retry_after": 30}
    assert response["Retry-After"] == "30"


def test_custom_api_exception_uses_default_code(api_client: APIClient):
    error = assert_envelope(api_client.get("/api/v1/teapot/"), 418, "teapot")
    assert error["message"] == "I am a teapot."


def test_request_id_in_envelope_matches_inbound_header(api_client: APIClient):
    response = api_client.get("/api/v1/forbidden/", headers={"X-Request-ID": "trace-99"})
    assert response.json()["error"]["request_id"] == "trace-99"
    assert response["X-Request-ID"] == "trace-99"


# ---------------------------------------------------------------- unhandled 500s
def test_unhandled_exception_returns_generic_500_and_logs_detail(
    api_client: APIClient, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.ERROR):
        response = api_client.get("/api/v1/boom/", headers={"X-Request-ID": "trace-500"})

    error = assert_envelope(response, 500, "internal_error")
    assert error["message"] == GENERIC_SERVER_ERROR
    assert error["details"] is None
    assert error["request_id"] == "trace-500"

    # Nothing about the failure reaches the client...
    raw = response.content.decode()
    for leaked in ("ValueError", "hunter2", "secret_module", "Traceback", "/srv/app"):
        assert leaked not in raw

    # ...but the full detail is logged.
    records = [r for r in caplog.records if r.name == "apps.core.exceptions"]
    assert len(records) == 1
    assert records[0].exc_info
    assert records[0].exc_info[0] is ValueError
    assert "BoomView" in records[0].getMessage()


def test_unhandled_exception_log_line_is_json_with_request_id_and_no_secrets(
    api_client: APIClient,
):
    """Same handler chain the LOGGING setting installs: JSON, redacted, with the ID."""
    config = settings.LOGGING["handlers"]["console"]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    handler.addFilter(RequestContextFilter())
    assert config["filters"] == ["redact", "request_context"]
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        api_client.get("/api/v1/boom/", headers={"X-Request-ID": "trace-json"})
    finally:
        root.removeHandler(handler)
    lines = [json.loads(x) for x in stream.getvalue().splitlines()]
    failure = next(r for r in lines if r["logger"] == "apps.core.exceptions")
    assert failure["request_id"] == "trace-json"
    assert failure["level"] == "ERROR"
    assert "ValueError" in failure["exception"]
    assert "hunter2" not in json.dumps(failure)
    assert "[REDACTED]" in failure["exception"]


@override_settings(DEBUG=True)
def test_debug_mode_names_exception_type_only(api_client: APIClient):
    response = api_client.get("/api/v1/boom/")
    error = assert_envelope(response, 500, "internal_error")
    assert error["details"] == {"exception": "ValueError"}
    assert "hunter2" not in response.content.decode()


def test_server_error_view_json_for_api_paths():
    response = server_error_view(RequestFactory().get("/api/v1/x/"))
    assert response.status_code == 500
    assert json.loads(response.content)["error"]["code"] == "internal_error"
    assert json.loads(response.content)["error"]["message"] == GENERIC_SERVER_ERROR


def test_server_error_view_default_page_elsewhere():
    response = server_error_view(RequestFactory().get("/admin/x/"))
    assert response.status_code == 500
    assert b'"error"' not in response.content


def test_error_payload_defaults_to_null_details_and_request_id():
    assert error_payload("x", "y") == {
        "error": {"code": "x", "message": "y", "details": None, "request_id": None}
    }
