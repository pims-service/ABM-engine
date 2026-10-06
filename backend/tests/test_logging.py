from __future__ import annotations

import io
import json
import logging

import pytest
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.core import logging as core_logging
from apps.core.logging import (
    REDACTED,
    JsonFormatter,
    RedactingFormatter,
    RedactionFilter,
    RequestContextFilter,
    build_logging_config,
    get_job_id,
    get_request_id,
    is_sensitive_key,
    redact,
    redact_text,
    reset_job_id,
    set_job_id,
    set_request_id,
)
from apps.core.middleware import REQUEST_ID_HEADER, RequestIDMiddleware, resolve_request_id

pytestmark = pytest.mark.unit


def _capture(formatter: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    """A logger wired like production: redact -> context -> formatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.addFilter(RedactionFilter())
    handler.addFilter(RequestContextFilter())
    logger = logging.getLogger(f"test.capture.{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


# ---------------------------------------------------------------- redaction
@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer abcdef123456.token", "abcdef123456"),
        ("authorization=Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ("login password=hunter2 ok", "hunter2"),
        ('{"password": "hunter2", "user": "bob"}', "hunter2"),
        ("{'api_key': 'sk-123456'}", "sk-123456"),
        ("refresh_token: abc.def.ghi", "abc.def"),
        ("Cookie: sessionid=abcdef; csrftoken=zzzz", "abcdef"),
        ("postgres://app:s3cretpw@db:5432/abm", "s3cretpw"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("got Bearer abcdefghijklmnop here", "abcdefghijklmnop"),
    ],
)
def test_redact_text_masks_secrets(raw, secret):
    cleaned = redact_text(raw)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_redact_text_leaves_normal_text_alone():
    text = "GET /api/v1/companies/ -> 200 for user bob"
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "key", ["password", "Authorization", "X-Api-Key", "access_token", "Cookie"]
)
def test_is_sensitive_key_true(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["username", "path", "status_code", "X-Request-ID"])
def test_is_sensitive_key_false(key):
    assert not is_sensitive_key(key)


def test_redact_recurses_into_structures():
    data = {"user": "bob", "password": "x", "nested": {"token": "t", "n": 1}, "l": ["password=y"]}
    out = redact(data)
    assert out == {
        "user": "bob",
        "password": REDACTED,
        "nested": {"token": REDACTED, "n": 1},
        "l": [f"password={REDACTED}"],
    }


def test_redact_stops_at_max_depth():
    deep: dict = {}
    cursor = deep
    for _ in range(12):
        cursor["a"] = {}
        cursor = cursor["a"]
    assert REDACTED in json.dumps(redact(deep))


def test_filter_redacts_message_args_and_extras():
    logger, stream = _capture(JsonFormatter())
    logger.info(
        "login with %s",
        "password=hunter2",
        extra={"password": "hunter2", "headers": {"Authorization": "Bearer abcdefghijkl"}},
    )
    line = stream.getvalue()
    assert "hunter2" not in line
    assert "abcdefghijkl" not in line
    record = json.loads(line)
    assert record["password"] == REDACTED
    assert record["headers"]["Authorization"] == REDACTED


def test_tracebacks_are_redacted():
    logger, stream = _capture(JsonFormatter())
    try:
        raise RuntimeError("connect failed password=hunter2")
    except RuntimeError:
        logger.exception("boom")
    record = json.loads(stream.getvalue())
    assert "hunter2" not in record["exception"]
    assert "RuntimeError" in record["exception"]


def test_plain_formatter_redacts_tracebacks():
    logger, stream = _capture(RedactingFormatter("%(request_id)s %(message)s"))
    try:
        raise RuntimeError("token=abc123")
    except RuntimeError:
        logger.exception("boom")
    assert "abc123" not in stream.getvalue()


# ---------------------------------------------------------------- JSON format
def test_json_formatter_shape_and_request_id():
    logger, stream = _capture(JsonFormatter())
    token = set_request_id("req-1")
    try:
        logger.warning("hello %s", "world", extra={"company_id": 7})
    finally:
        core_logging.reset_request_id(token)
    record = json.loads(stream.getvalue())
    assert record["message"] == "hello world"
    assert record["level"] == "WARNING"
    assert record["logger"].startswith("test.capture")
    assert record["request_id"] == "req-1"
    assert record["company_id"] == 7
    assert record["timestamp"].endswith("+00:00")
    assert "job_id" not in record


def test_json_formatter_without_request_id_is_null():
    logger, stream = _capture(JsonFormatter())
    logger.info("outside a request")
    assert json.loads(stream.getvalue())["request_id"] is None


def test_json_formatter_includes_job_id_and_stack_info():
    logger, stream = _capture(JsonFormatter())
    token = set_job_id("job-9")
    try:
        assert get_job_id() == "job-9"
        logger.info("working", stack_info=True)
    finally:
        reset_job_id(token)
    record = json.loads(stream.getvalue())
    assert record["job_id"] == "job-9"
    assert "stack_info" in record
    assert get_job_id() is None


def test_dev_format_includes_request_id():
    logger, stream = _capture(RedactingFormatter("%(levelname)s [%(request_id)s] %(message)s"))
    token = set_request_id("abc")
    try:
        logger.info("hi")
    finally:
        core_logging.reset_request_id(token)
    assert stream.getvalue().strip() == "INFO [abc] hi"


def test_build_logging_config_switches_formatter():
    assert build_logging_config(json_logs=True)["formatters"]["default"]["()"].endswith(
        "JsonFormatter"
    )
    dev = build_logging_config(json_logs=False, level="DEBUG")
    assert "format" in dev["formatters"]["default"]
    assert dev["root"]["level"] == "DEBUG"


def test_logging_config_is_applied_in_settings():
    root_handlers = logging.getLogger().handlers
    assert any(
        {type(f) for f in h.filters} >= {RedactionFilter, RequestContextFilter}
        for h in root_handlers
    )


# ---------------------------------------------------------------- middleware
def test_resolve_request_id_accepts_valid_inbound():
    request = RequestFactory().get("/", headers={REQUEST_ID_HEADER: "abc-123.X_y"})
    assert resolve_request_id(request) == "abc-123.X_y"


@pytest.mark.parametrize("bad", ["", "has space", "new\nline", "x" * 129, "semi;colon", "<script>"])
def test_resolve_request_id_replaces_invalid_inbound(bad):
    request = RequestFactory().get("/")
    request.META["HTTP_X_REQUEST_ID"] = bad
    generated = resolve_request_id(request)
    assert generated != bad
    assert len(generated) == 32


def test_middleware_sets_header_and_clears_context():
    def view(request):
        from django.http import HttpResponse

        assert get_request_id() == request.request_id
        return HttpResponse("ok")

    response = RequestIDMiddleware(view)(RequestFactory().get("/", headers={"X-Request-ID": "r1"}))
    assert response[REQUEST_ID_HEADER] == "r1"
    assert get_request_id() is None


def test_middleware_clears_context_when_view_raises():
    def view(request):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        RequestIDMiddleware(view)(RequestFactory().get("/"))
    assert get_request_id() is None


@pytest.mark.api
def test_response_has_generated_request_id(api_client: APIClient):
    response = api_client.get("/api/v1/")
    assert len(response[REQUEST_ID_HEADER]) == 32


@pytest.mark.api
def test_response_echoes_inbound_request_id(api_client: APIClient):
    response = api_client.get("/api/v1/", headers={"X-Request-ID": "trace-42"})
    assert response[REQUEST_ID_HEADER] == "trace-42"


@pytest.mark.api
@pytest.mark.parametrize(("path", "level"), [("/api/v1/", "INFO"), ("/api/v1/nope/", "WARNING")])
def test_every_request_log_line_carries_request_id(api_client, path, level):
    """Includes Django's own ``django.request`` line, which is emitted after the middleware."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        api_client.get(path, headers={"X-Request-ID": "trace-7"})
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    access = [r for r in lines if r["logger"] == "apps.core.access"]
    assert access
    assert all(r["request_id"] == "trace-7" for r in lines)
    assert "request" not in access[0]
    assert access[0]["level"] == level
    assert access[0]["path"] == path
    assert "duration_ms" in access[0]
