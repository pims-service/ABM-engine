"""Anthropic adapter, transport and secret-hygiene tests (issue #81). No network: every test
injects a fake transport or a fake connection."""

from __future__ import annotations

import http.client
import json
import logging
from collections.abc import Callable
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from apps.integrations.adapters.anthropic import (
    AnthropicAdapter,
    HttpRequest,
    HttpResponse,
    UrllibTransport,
    prepare_schema,
)
from apps.integrations.framework import (
    PromptSpec,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderConflict,
    ProviderError,
    ProviderNotConfigured,
    ProviderNotFound,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUpstreamError,
    StructuredSchema,
)
from apps.integrations.llm import StructuredOutputError, pydantic_schema, structured_call
from tests.adapter_contract import LLMContract

pytestmark = pytest.mark.unit

KEY = "YOUR_API_KEY"  # pragma: allowlist secret
MODEL = "claude-sonnet-5-5"


def message(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 7},
        **extra,
    }


class FakeTransport:
    """Records requests; answers with ``responder(request)`` or a fixed status/body."""

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
        responder: Callable[[HttpRequest], HttpResponse] | None = None,
    ) -> None:
        self.requests: list[HttpRequest] = []
        self._responder = responder
        self._fixed = HttpResponse(
            status, {"request-id": "req_123", **(headers or {})}, json.dumps(body or {}).encode()
        )

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._responder(request) if self._responder else self._fixed


def routed(text: str = '{"a": 1}') -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        body = {"data": []} if "/v1/models" in request.url else message(text)
        return HttpResponse(200, {"request-id": "req_9"}, json.dumps(body).encode())

    return FakeTransport(responder=responder)


def adapter_with(transport: FakeTransport, **config: Any) -> AnthropicAdapter:
    return AnthropicAdapter({"model": MODEL, **config}, {"api_key": KEY}, transport=transport)


ECHO = StructuredSchema("echo", {"type": "object"}, lambda raw: dict(raw))
PROMPT = PromptSpec("classify", "2", "Hello", system="Be brief.")


# ------------------------------------------------------------------ contract suite
class TestAnthropicContract(LLMContract):
    adapter_cls = AnthropicAdapter

    def make_config(self) -> dict[str, Any]:
        return {"model": MODEL}

    def make_adapter(self) -> Any:
        return self.adapter_cls(self.make_config(), self.make_credentials(), transport=routed())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        transport = FakeTransport(status=429, headers={"retry-after": "5"})
        return self.adapter_cls(self.make_config(), self.make_credentials(), transport=transport)


# ------------------------------------------------------------------ request and response
class TestGenerateStructured:
    def test_request_shape_follows_the_documented_api(self) -> None:
        transport = routed()
        adapter = adapter_with(transport)
        adapter.generate_structured(PROMPT, ECHO, max_tokens=300)
        request = transport.requests[0]
        assert request.method == "POST"
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == KEY
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["content-type"] == "application/json"
        assert request.timeout == 60
        body = json.loads(request.body or b"")
        assert body["model"] == MODEL
        assert body["max_tokens"] == 300
        assert body["system"] == "Be brief."
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
        assert "temperature" not in body

    def test_temperature_is_sent_only_when_enabled(self) -> None:
        transport = routed()
        adapter_with(transport, send_temperature=True).generate_structured(
            PROMPT, ECHO, temperature=0.2
        )
        assert json.loads(transport.requests[0].body or b"")["temperature"] == 0.2

    def test_result_has_usage_model_prompt_version_and_request_id(self) -> None:
        result = adapter_with(routed()).generate_structured(PROMPT, ECHO)
        assert result.data == {"a": 1}
        assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 7)
        assert result.usage.model == MODEL
        assert result.provenance.source_ref == "prompt:classify@2"
        assert result.provenance.request_id == "req_9"
        assert result.provenance.terms == "licensed_api"

    def test_pydantic_end_to_end_with_structured_call(self) -> None:
        class Out(BaseModel):
            label: str

        transport = routed('{"label": "fit"}')
        result = structured_call(adapter_with(transport), PROMPT, pydantic_schema(Out))
        assert result.data == Out(label="fit")

    def test_invalid_output_repairs_once_over_the_wire_and_sums_usage(self) -> None:
        class Out(BaseModel):
            label: str

        replies = iter(['{"label": 5}', '{"label": "fit"}'])

        def responder(request: HttpRequest) -> HttpResponse:
            return HttpResponse(200, {}, json.dumps(message(next(replies))).encode())

        transport = FakeTransport(responder=responder)
        result = structured_call(adapter_with(transport), PROMPT, pydantic_schema(Out))
        assert result.data == Out(label="fit")
        assert len(transport.requests) == 2
        assert result.usage.input_tokens == 24
        assert result.usage.output_tokens == 14
        second = json.loads(transport.requests[1].body or b"")["messages"][0]["content"]
        assert "label (string_type)" in second

    def test_two_invalid_answers_raise_typed_error_after_two_calls(self) -> None:
        class Out(BaseModel):
            label: str

        transport = routed('{"label": 5}')
        with pytest.raises(StructuredOutputError) as info:
            structured_call(adapter_with(transport), PROMPT, pydantic_schema(Out))
        assert len(transport.requests) == 2
        assert info.value.usage.input_tokens == 24

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            (message("not json"), "json_invalid"),
            (message("[1, 2]"), None),
            ({**message("{}"), "content": []}, "no_text"),
            ({**message("{}"), "content": [{"type": "thinking", "thinking": "x"}]}, "no_text"),
            (message("{}", stop_reason="refusal"), "stop_refusal"),
            (message("{", stop_reason="max_tokens"), "stop_max_tokens"),
        ],
    )
    def test_unusable_model_answers_are_invalid_output_with_usage(
        self, body: dict[str, Any], code: str | None
    ) -> None:
        schema = StructuredSchema("d", {"type": "object"}, lambda raw: dict(raw))
        with pytest.raises(StructuredOutputError) as info:
            adapter_with(FakeTransport(body)).generate_structured(PROMPT, schema)
        assert info.value.usage.input_tokens == 12
        if code:
            assert info.value.issues[0].code == code

    def test_text_block_after_a_thinking_block_is_used(self) -> None:
        body = message("{}")
        body["content"] = [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": '{"b": 2}'},
        ]
        assert adapter_with(FakeTransport(body)).generate_structured(PROMPT, ECHO).data == {"b": 2}

    @pytest.mark.parametrize("body", [None, [], {"content": "x"}])
    def test_malformed_envelope_is_an_upstream_error(self, body: Any) -> None:
        transport = FakeTransport(body)
        if body is None:
            transport = FakeTransport(responder=lambda r: HttpResponse(200, {}, b"<html>"))
        with pytest.raises(ProviderUpstreamError):
            adapter_with(transport).generate_structured(PROMPT, ECHO)

    def test_bad_arguments_are_refused_before_any_request(self) -> None:
        transport = routed()
        adapter = adapter_with(transport)
        for kwargs in ({"temperature": 1.5}, {"temperature": -1}, {"max_tokens": 0}):
            with pytest.raises(ProviderBadRequest):
                adapter.generate_structured(PROMPT, ECHO, **kwargs)
        assert transport.requests == []


class TestPrepareSchema:
    def test_objects_get_additional_properties_false_and_constraints_are_dropped(self) -> None:
        class Inner(BaseModel):
            n: int

        class Out(BaseModel):
            inner: Inner
            names: list[str]
            minimum: str  # a property NAMED like a keyword must survive

        schema = Out.model_json_schema() | {
            "properties": {
                **Out.model_json_schema()["properties"],
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
                "tag": {"type": "string", "minLength": 2, "maxLength": 9, "enum": ["minimum"]},
                "few": {"type": "array", "minItems": 3, "maxItems": 4, "items": {"type": "string"}},
            }
        }
        prepared = prepare_schema(schema)
        assert prepared["additionalProperties"] is False
        assert prepared["$defs"]["Inner"]["additionalProperties"] is False
        props = prepared["properties"]
        assert "minimum" in props
        assert props["score"] == {"type": "integer"}
        assert props["tag"] == {"type": "string", "enum": ["minimum"]}
        assert props["few"] == {"type": "array", "items": {"type": "string"}}
        assert schema["properties"]["score"]["minimum"] == 0  # input untouched


# ------------------------------------------------------------------ test_connection
class TestConnection:
    def test_cheap_models_call_and_safe_result(self) -> None:
        transport = routed()
        adapter = adapter_with(transport)
        result = adapter.test_connection()
        assert result.ok
        request = transport.requests[0]
        assert (request.method, request.url) == (
            "GET",
            "https://api.anthropic.com/v1/models?limit=1",
        )
        assert request.body is None
        assert AnthropicAdapter.capabilities.test_connection_costs_credits is False

    def test_failure_has_code_and_fixed_message(self) -> None:
        result = adapter_with(
            FakeTransport(status=401, body={"error": {"message": KEY}})
        ).test_connection()
        assert result.ok is False
        assert result.error_code == "provider_auth"
        assert result.message == ProviderAuthError.message
        assert KEY not in repr(result.as_dict())

    def test_unexpected_success_body_fails(self) -> None:
        assert adapter_with(FakeTransport({"nope": 1})).test_connection().ok is False


# ------------------------------------------------------------------ status mapping
class TestStatusMapping:
    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (400, ProviderBadRequest),
            (401, ProviderAuthError),
            (402, ProviderQuotaExceeded),
            (403, ProviderAuthError),
            (404, ProviderNotFound),
            (409, ProviderConflict),
            (413, ProviderBadRequest),
            (429, ProviderRateLimited),
            (500, ProviderUnavailable),
            (504, ProviderTimeout),
            (529, ProviderUnavailable),
            (302, ProviderUpstreamError),
        ],
    )
    def test_each_documented_status(self, status: int, error: type[ProviderError]) -> None:
        body = {"type": "error", "error": {"type": "x", "message": f"leak {KEY}"}}
        with pytest.raises(error) as info:
            adapter_with(FakeTransport(body, status=status)).generate_structured(PROMPT, ECHO)
        assert type(info.value) is error
        assert info.value.upstream_status == status
        assert KEY not in str(info.value)
        assert "leak" not in str(info.value)

    def test_retry_after_is_parsed(self) -> None:
        transport = FakeTransport(status=429, headers={"retry-after": "17"})
        with pytest.raises(ProviderRateLimited) as info:
            adapter_with(transport).generate_structured(PROMPT, ECHO)
        assert info.value.retry_after == 17
        assert info.value.retryable

    def test_unparseable_retry_after_is_ignored(self) -> None:
        transport = FakeTransport(status=429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00"})
        with pytest.raises(ProviderRateLimited) as info:
            adapter_with(transport).generate_structured(PROMPT, ECHO)
        assert info.value.retry_after is None


# ------------------------------------------------------------------ config and secrets
class TestConfigAndSecrets:
    def test_model_is_required_and_has_no_default(self) -> None:
        props = AnthropicAdapter.config_schema["properties"]
        assert "default" not in props["model"]
        assert "model" in AnthropicAdapter.config_schema["required"]
        with pytest.raises(ProviderNotConfigured) as info:
            AnthropicAdapter({}, {"api_key": KEY})
        assert "model" in info.value.fields
        assert not AnthropicAdapter.validate_config({"model": "  "}, {"api_key": KEY}).ok

    def test_defaults(self) -> None:
        adapter = AnthropicAdapter({"model": MODEL}, {"api_key": KEY})
        assert adapter.config["api_base"] == "https://api.anthropic.com"
        assert adapter.config["anthropic_version"] == "2023-06-01"
        assert adapter.config["timeout_seconds"] == 60

    @pytest.mark.parametrize(
        "api_base",
        [
            "http://api.anthropic.com",
            "https://evil.example.com",
            "https://api.anthropic.com.evil.example.com",
            "https://user:pw@api.anthropic.com",
            "https://api.anthropic.com:8443",
            "https://api.anthropic.com/?x=1",
            "ftp://api.anthropic.com",
            "https://169.254.169.254",
        ],
    )
    def test_api_base_must_be_the_allow_listed_https_host(self, api_base: str) -> None:
        result = AnthropicAdapter.validate_config(
            {"model": MODEL, "api_base": api_base}, {"api_key": KEY}
        )
        assert ("api_base", "enum") in {(i.field, i.code) for i in result.issues}

    def test_timeout_is_bounded(self) -> None:
        for value in (0, 301):
            assert not AnthropicAdapter.validate_config(
                {"model": MODEL, "timeout_seconds": value}, {"api_key": KEY}
            ).ok
        transport = routed()
        adapter_with(transport, timeout_seconds=12).generate_structured(PROMPT, ECHO)
        assert transport.requests[0].timeout == 12

    def test_key_never_in_repr_logs_or_errors(self, caplog: pytest.LogCaptureFixture) -> None:
        transport = FakeTransport(status=401, body={"error": KEY})
        adapter = adapter_with(transport)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ProviderAuthError) as info:
                structured_call(adapter, PROMPT, ECHO)
            adapter.test_connection()
        request = transport.requests[0]
        shown = " ".join(
            [
                repr(adapter),
                str(info.value),
                repr(info.value.as_dict()),
                repr(request),
                repr(HttpResponse(200, {"x": KEY}, KEY.encode())),
                caplog.text,
            ]
        )
        assert KEY not in shown
        assert KEY not in repr(adapter.credential("api_key"))

    def test_nothing_is_registered_on_import(self) -> None:
        from apps.integrations.framework import default_registry

        assert not any(AnthropicAdapter.key == k for _, k in _slots(default_registry))


def _slots(registry: Any) -> list[tuple[str, str]]:
    return [(d["category"], d["key"]) for d in registry.describe()]


# ---- real transport (fake connection)
class FakeRawResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self._body = body
        self._headers = headers

    def getheader(self, name: str) -> str | None:
        return {k.lower(): v for k, v in self._headers.items()}.get(name.lower())

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers.items())

    def read(self, amt: int) -> bytes:
        return self._body[:amt]


class FakeConnection:
    instances: ClassVar[list[FakeConnection]] = []
    response: FakeRawResponse | None = None
    error: BaseException | None = None

    def __init__(self, host: str, port: int, timeout: float, context: Any) -> None:
        self.args = (host, port, timeout, context)
        self.requested: tuple[Any, ...] = ()
        self.closed = False
        FakeConnection.instances.append(self)

    def request(self, method: str, target: str, body: Any = None, headers: Any = None) -> None:
        self.requested = (method, target, body, headers)
        if FakeConnection.error:
            raise FakeConnection.error

    def getresponse(self) -> FakeRawResponse:
        assert FakeConnection.response is not None
        return FakeConnection.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_conn(monkeypatch: pytest.MonkeyPatch) -> type[FakeConnection]:
    FakeConnection.instances = []
    FakeConnection.response = FakeRawResponse(200, b'{"ok": true}', {"Request-Id": "r1"})
    FakeConnection.error = None
    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    return FakeConnection


def real(**kwargs: Any) -> UrllibTransport:
    return UrllibTransport(("api.anthropic.com",), provider_name="anthropic", **kwargs)


class TestUrllibTransport:
    def request(self, url: str = "https://api.anthropic.com/v1/messages?limit=1") -> HttpRequest:
        return HttpRequest("POST", url, {"x-api-key": KEY}, b"{}", timeout=7)

    def test_sends_over_verified_tls_with_timeout_and_lowercases_headers(
        self, fake_conn: type[FakeConnection]
    ) -> None:
        response = real().send(self.request())
        conn = fake_conn.instances[0]
        host, port, timeout, context = conn.args
        assert (host, port, timeout) == ("api.anthropic.com", 443, 7)
        import ssl

        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert conn.requested[:2] == ("POST", "/v1/messages?limit=1")
        assert conn.closed
        assert response.status == 200
        assert response.headers["request-id"] == "r1"
        assert response.body == b'{"ok": true}'

    @pytest.mark.parametrize(
        "url",
        [
            "http://api.anthropic.com/v1/messages",
            "https://evil.example.com/v1/messages",
            "https://api.anthropic.com@evil.example.com/",
            "https://api.anthropic.com:444/v1/messages",
            "https://user:pw@api.anthropic.com/v1/messages",
            "file:///etc/passwd",
            "https:///v1",
        ],
    )
    def test_host_allow_list_and_scheme(self, fake_conn: type[FakeConnection], url: str) -> None:
        with pytest.raises(ProviderBadRequest):
            real().send(self.request(url))
        assert fake_conn.instances == []

    def test_response_size_cap_by_body_and_by_header(self, fake_conn: type[FakeConnection]) -> None:
        fake_conn.response = FakeRawResponse(200, b"x" * 11, {})
        with pytest.raises(ProviderUpstreamError):
            real(max_response_bytes=10).send(self.request())
        fake_conn.response = FakeRawResponse(200, b"x", {"Content-Length": "999999"})
        with pytest.raises(ProviderUpstreamError):
            real(max_response_bytes=10).send(self.request())
        fake_conn.response = FakeRawResponse(200, b"x" * 10, {})
        assert real(max_response_bytes=10).send(self.request()).body == b"x" * 10

    def test_timeouts_and_network_errors_are_typed_and_text_is_dropped(
        self, fake_conn: type[FakeConnection]
    ) -> None:
        fake_conn.error = TimeoutError("timed out to 10.0.0.1")
        with pytest.raises(ProviderTimeout) as info:
            real().send(self.request())
        assert "10.0.0.1" not in str(info.value)
        assert fake_conn.instances[-1].closed
        for exc in (ConnectionResetError("reset"), http.client.BadStatusLine("x"), OSError("dns")):
            fake_conn.error = exc
            with pytest.raises(ProviderUnavailable) as unavailable:
                real().send(self.request())
            assert unavailable.value.__cause__ is None

    def test_redirects_are_returned_not_followed(self, fake_conn: type[FakeConnection]) -> None:
        fake_conn.response = FakeRawResponse(302, b"", {"Location": "https://evil.example.com"})
        assert real().send(self.request()).status == 302
        assert len(fake_conn.instances) == 1

    def test_default_transport_of_the_adapter_uses_the_allow_list(self) -> None:
        adapter = AnthropicAdapter({"model": MODEL}, {"api_key": KEY})
        assert isinstance(adapter._transport, UrllibTransport)
        assert adapter._transport._allowed_hosts == ("api.anthropic.com",)
