"""OpenAI adapter tests. No network: every test injects a fake transport."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.openai import (
    DEFAULT_OUTPUT_HEADROOM,
    OUTPUT_TOKEN_CAP,
    OpenAIAdapter,
    schema_name,
)
from apps.integrations.framework import (
    PromptSpec,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderNotConfigured,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUpstreamError,
    StructuredSchema,
)
from apps.integrations.llm import StructuredOutputError, pydantic_schema, structured_call
from tests.adapter_contract import LLMContract

pytestmark = pytest.mark.unit

KEY = "YOUR_API_KEY"  # pragma: allowlist secret
MODEL = "gpt-4o"


def completion(text: str | None, **choice: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "model": "gpt-4o-2026-01-01",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
                **choice,
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
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
        self._fixed = HttpResponse(status, headers or {}, json.dumps(body or {}).encode())

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._responder(request) if self._responder else self._fixed


def routed(text: str = '{"a": 1}') -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        body = {"id": MODEL, "object": "model"} if request.method == "GET" else completion(text)
        return HttpResponse(200, {"x-request-id": "req_9"}, json.dumps(body).encode())

    return FakeTransport(responder=responder)


def adapter_with(transport: FakeTransport, **config: Any) -> OpenAIAdapter:
    return OpenAIAdapter({"model": MODEL, **config}, {"api_key": KEY}, transport=transport)


def body_of(transport: FakeTransport, index: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(transport.requests[index].body or b"")
    return body


ECHO = StructuredSchema("echo", {"type": "object"}, lambda raw: dict(raw))
PROMPT = PromptSpec("classify", "2", "Hello", system="Be brief.")


class TestOpenAIContract(LLMContract):
    adapter_cls = OpenAIAdapter

    def make_config(self) -> dict[str, Any]:
        return {"model": MODEL}

    def make_adapter(self) -> Any:
        return self.adapter_cls(self.make_config(), self.make_credentials(), transport=routed())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        transport = FakeTransport(status=429, headers={"retry-after": "5"})
        return self.adapter_cls(self.make_config(), self.make_credentials(), transport=transport)


class TestGenerateStructured:
    def test_request_shape_follows_the_documented_api(self) -> None:
        transport = routed()
        adapter_with(transport).generate_structured(PROMPT, ECHO, max_tokens=300)
        request = transport.requests[0]
        assert request.method == "POST"
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.timeout == 60
        body = body_of(transport)
        assert body["model"] == MODEL
        assert body["messages"] == [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hello"},
        ]
        assert body["max_completion_tokens"] == 300 + DEFAULT_OUTPUT_HEADROOM
        assert "max_tokens" not in body
        assert "temperature" not in body
        assert body["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "echo", "schema": {"type": "object"}, "strict": False},
        }

    def test_no_system_prompt_sends_only_the_user_message(self) -> None:
        transport = routed()
        adapter_with(transport).generate_structured(PromptSpec("p", "1", "Hi"), ECHO)
        assert body_of(transport)["messages"] == [{"role": "user", "content": "Hi"}]

    def test_temperature_is_sent_only_when_enabled(self) -> None:
        transport = routed()
        adapter_with(transport, send_temperature=True).generate_structured(
            PROMPT, ECHO, temperature=0.2
        )
        assert body_of(transport)["temperature"] == 0.2

    @pytest.mark.parametrize(
        ("config", "max_tokens", "expected"),
        [
            ({}, 1500, 1500 + DEFAULT_OUTPUT_HEADROOM),
            ({"output_headroom": 0}, 1500, 1500),
            ({"output_headroom": 2000}, 1500, 3500),
            ({}, 12_000, OUTPUT_TOKEN_CAP),
            ({}, 100_000, 100_000),
        ],
    )
    def test_output_limit_leaves_room_for_reasoning(
        self, config: dict[str, Any], max_tokens: int, expected: int
    ) -> None:
        transport = routed()
        adapter_with(transport, **config).generate_structured(PROMPT, ECHO, max_tokens=max_tokens)
        assert body_of(transport)["max_completion_tokens"] == expected

    def test_schema_name_is_made_safe(self) -> None:
        assert schema_name("facts extraction v2!") == "facts_extraction_v2_"
        assert len(schema_name("x" * 100)) == 64
        assert schema_name("") == "answer"

    def test_result_has_usage_model_prompt_version_and_request_id(self) -> None:
        result = adapter_with(routed()).generate_structured(PROMPT, ECHO)
        assert result.data == {"a": 1}
        assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 7)
        assert result.usage.model == "gpt-4o-2026-01-01"
        assert result.provenance.source_ref == "prompt:classify@2"
        assert result.provenance.request_id == "req_9"
        assert result.provenance.terms == "licensed_api"

    def test_pydantic_end_to_end_with_structured_call(self) -> None:
        class Out(BaseModel):
            label: str

        result = structured_call(
            adapter_with(routed('{"label": "fit"}')), PROMPT, pydantic_schema(Out)
        )
        assert result.data == Out(label="fit")

    def test_invalid_output_repairs_once_over_the_wire_and_sums_usage(self) -> None:
        class Out(BaseModel):
            label: str

        replies = iter(['{"label": 5}', '{"label": "fit"}'])

        def responder(request: HttpRequest) -> HttpResponse:
            return HttpResponse(200, {}, json.dumps(completion(next(replies))).encode())

        transport = FakeTransport(responder=responder)
        result = structured_call(adapter_with(transport), PROMPT, pydantic_schema(Out))
        assert result.data == Out(label="fit")
        assert len(transport.requests) == 2
        assert (result.usage.input_tokens, result.usage.output_tokens) == (24, 14)

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            (completion("not json"), "json_invalid"),
            (completion("[1, 2]"), None),
            (completion(None), "no_text"),
            (completion("  "), "no_text"),
            (completion("{}", finish_reason="length"), "stop_length"),
            (completion("{}", finish_reason="content_filter"), "stop_content_filter"),
            (
                completion(
                    None,
                    message={"role": "assistant", "content": None, "refusal": "I can't help."},
                ),
                "stop_refusal",
            ),
        ],
    )
    def test_unusable_model_answers_are_invalid_output_with_usage(
        self, body: dict[str, Any], code: str | None
    ) -> None:
        with pytest.raises(StructuredOutputError) as info:
            adapter_with(FakeTransport(body)).generate_structured(PROMPT, ECHO)
        assert info.value.usage.input_tokens == 12
        if code:
            assert info.value.issues[0].code == code

    @pytest.mark.parametrize(
        "body",
        [None, [], {"choices": "x"}, {"choices": []}, {"choices": ["x"]}, {"choices": [{}]}],
    )
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


class TestConnection:
    def test_gets_the_configured_model_and_the_result_is_safe(self) -> None:
        transport = routed()
        assert adapter_with(transport).test_connection().ok
        request = transport.requests[0]
        assert (request.method, request.url) == (
            "GET",
            f"https://api.openai.com/v1/models/{MODEL}",
        )
        assert request.body is None
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert OpenAIAdapter.capabilities.test_connection_costs_credits is False

    def test_a_wrong_model_name_is_reported_as_not_found(self) -> None:
        result = adapter_with(FakeTransport(status=404)).test_connection()
        assert result.ok is False
        assert result.error_code == ProviderNotFound.code

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


class TestStatusMapping:
    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (400, ProviderBadRequest),
            (401, ProviderAuthError),
            (403, ProviderAuthError),
            (404, ProviderNotFound),
            (429, ProviderRateLimited),
        ],
    )
    def test_each_status(self, status: int, error: type[ProviderError]) -> None:
        with pytest.raises(error):
            adapter_with(FakeTransport(status=status)).generate_structured(PROMPT, ECHO)

    def test_retry_after_is_parsed(self) -> None:
        transport = FakeTransport(status=429, headers={"retry-after": "42"})
        with pytest.raises(ProviderRateLimited) as info:
            adapter_with(transport).generate_structured(PROMPT, ECHO)
        assert info.value.retry_after == 42


class TestConfigAndSecrets:
    def test_model_is_required_and_has_no_default(self) -> None:
        assert "default" not in OpenAIAdapter.config_schema["properties"]["model"]
        with pytest.raises(ProviderNotConfigured) as info:
            OpenAIAdapter({}, {"api_key": KEY})
        assert "model" in info.value.fields
        assert not OpenAIAdapter.validate_config({"model": "  "}, {"api_key": KEY}).ok

    def test_defaults(self) -> None:
        adapter = OpenAIAdapter({"model": MODEL}, {"api_key": KEY})
        assert adapter.config["api_base"] == "https://api.openai.com"
        assert adapter.config["send_temperature"] is False
        assert adapter.config["timeout_seconds"] == 60
        assert adapter.config["output_headroom"] == DEFAULT_OUTPUT_HEADROOM

    @pytest.mark.parametrize(
        "model",
        ["gpt 4o", "gpt/../x", "gpt-4o?x=1", "gpt-4o#f", "-lead", "a" * 101, "models/gpt"],
    )
    def test_model_must_be_a_plain_id_because_it_goes_into_the_url(self, model: str) -> None:
        assert "model" in OpenAIAdapter.validate_config({"model": model}, {"api_key": KEY}).fields

    @pytest.mark.parametrize("model", [MODEL, "o3", "gpt-4.1-mini", "ft:gpt-4o:acme::abc123"])
    def test_ordinary_model_ids_are_accepted(self, model: str) -> None:
        assert OpenAIAdapter.validate_config({"model": model}, {"api_key": KEY}).ok

    @pytest.mark.parametrize(
        "api_base",
        [
            "http://api.openai.com",
            "https://evil.example.com",
            "https://api.openai.com.evil.example.com",
            "https://user:" + "pw@api.openai.com",
            "https://api.openai.com:8443",
            "https://api.openai.com/?x=1",
            "https://169.254.169.254",
        ],
    )
    def test_api_base_must_be_the_allow_listed_https_host(self, api_base: str) -> None:
        result = OpenAIAdapter.validate_config(
            {"model": MODEL, "api_base": api_base}, {"api_key": KEY}
        )
        assert ("api_base", "enum") in {(i.field, i.code) for i in result.issues}

    def test_numbers_are_bounded(self) -> None:
        for config in (
            {"timeout_seconds": 0},
            {"timeout_seconds": 301},
            {"output_headroom": -1},
            {"output_headroom": OUTPUT_TOKEN_CAP + 1},
            {"output_headroom": 1.5},
        ):
            assert not OpenAIAdapter.validate_config(
                {"model": MODEL, **config}, {"api_key": KEY}
            ).ok

    def test_key_never_in_repr_logs_or_errors(self, caplog: pytest.LogCaptureFixture) -> None:
        transport = FakeTransport(status=401, body={"error": KEY})
        adapter = adapter_with(transport)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ProviderAuthError) as info:
                structured_call(adapter, PROMPT, ECHO)
            adapter.test_connection()
        shown = " ".join(
            [
                repr(adapter),
                str(info.value),
                repr(info.value.as_dict()),
                repr(transport.requests[0]),
                caplog.text,
            ]
        )
        assert KEY not in shown
        assert KEY not in repr(adapter.credential("api_key"))

    def test_nothing_is_registered_on_import(self) -> None:
        from apps.integrations.framework import default_registry

        slots = [(d["category"], d["key"]) for d in default_registry.describe()]
        assert not any(OpenAIAdapter.key == key for _, key in slots)
