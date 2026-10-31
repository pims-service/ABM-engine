"""Gemini adapter tests. No network: every test injects a fake transport."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.gemini import (
    DEFAULT_THINKING_HEADROOM,
    OUTPUT_TOKEN_CAP,
    STYLE_RESPONSE_FORMAT,
    GeminiAdapter,
    prepare_schema,
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
MODEL = "gemini-2.5-flash"


def answer(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7},
        "modelVersion": MODEL,
        "responseId": "resp_1",
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
        self._fixed = HttpResponse(status, headers or {}, json.dumps(body or {}).encode())

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._responder(request) if self._responder else self._fixed


def routed(text: str = '{"a": 1}') -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        body = {"name": f"models/{MODEL}"} if request.method == "GET" else answer(text)
        return HttpResponse(200, {}, json.dumps(body).encode())

    return FakeTransport(responder=responder)


def adapter_with(transport: FakeTransport, **config: Any) -> GeminiAdapter:
    return GeminiAdapter({"model": MODEL, **config}, {"api_key": KEY}, transport=transport)


def body_of(transport: FakeTransport, index: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(transport.requests[index].body or b"")
    return body


ECHO = StructuredSchema("echo", {"type": "object"}, lambda raw: dict(raw))
PROMPT = PromptSpec("classify", "2", "Hello", system="Be brief.")


# ------------------------------------------------------------------ contract suite
class TestGeminiContract(LLMContract):
    adapter_cls = GeminiAdapter

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
        adapter_with(transport).generate_structured(PROMPT, ECHO, max_tokens=300)
        request = transport.requests[0]
        assert request.method == "POST"
        assert request.url == (
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
        )
        assert request.headers["x-goog-api-key"] == KEY
        assert request.headers["content-type"] == "application/json"
        assert "key=" not in request.url
        assert request.timeout == 60
        body = body_of(transport)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "Hello"}]}]
        assert body["systemInstruction"] == {"parts": [{"text": "Be brief."}]}
        config = body["generationConfig"]
        assert config["maxOutputTokens"] == 300 + DEFAULT_THINKING_HEADROOM
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"] == {"type": "object"}
        assert config["temperature"] == 0.0
        assert "thinkingConfig" not in config
        assert "responseFormat" not in config

    def test_a_models_prefix_is_not_doubled(self) -> None:
        transport = routed()
        adapter_with(transport, model=f"models/{MODEL}").generate_structured(PROMPT, ECHO)
        assert transport.requests[0].url.endswith(f"/v1beta/models/{MODEL}:generateContent")

    def test_no_system_prompt_sends_no_system_instruction(self) -> None:
        transport = routed()
        adapter_with(transport).generate_structured(PromptSpec("p", "1", "Hi"), ECHO)
        assert "systemInstruction" not in body_of(transport)

    def test_the_newer_response_format_spelling_can_be_chosen(self) -> None:
        transport = routed()
        adapter_with(transport, structured_output=STYLE_RESPONSE_FORMAT).generate_structured(
            PROMPT, ECHO
        )
        config = body_of(transport)["generationConfig"]
        assert config["responseFormat"] == {
            "text": {"mimeType": "APPLICATION_JSON", "schema": {"type": "object"}}
        }
        assert "responseMimeType" not in config
        assert "responseJsonSchema" not in config

    def test_temperature_can_be_left_out_and_thinking_budget_is_sent_only_when_set(self) -> None:
        transport = routed()
        adapter_with(transport, send_temperature=False, thinking_budget=0).generate_structured(
            PROMPT, ECHO, temperature=0.2
        )
        config = body_of(transport)["generationConfig"]
        assert "temperature" not in config
        assert config["thinkingConfig"] == {"thinkingBudget": 0}

    @pytest.mark.parametrize(
        ("config", "max_tokens", "expected"),
        [
            ({}, 1500, 1500 + DEFAULT_THINKING_HEADROOM),
            ({"thinking_budget": 0}, 1500, 1500),
            ({"thinking_budget": 2000}, 1500, 3500),
            ({}, 60_000, OUTPUT_TOKEN_CAP),
            ({}, 100_000, 100_000),
        ],
    )
    def test_output_limit_leaves_room_for_thinking(
        self, config: dict[str, Any], max_tokens: int, expected: int
    ) -> None:
        transport = routed()
        adapter_with(transport, **config).generate_structured(PROMPT, ECHO, max_tokens=max_tokens)
        assert body_of(transport)["generationConfig"]["maxOutputTokens"] == expected

    def test_result_has_usage_model_prompt_version_and_response_id(self) -> None:
        result = adapter_with(routed()).generate_structured(PROMPT, ECHO)
        assert result.data == {"a": 1}
        assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 7)
        assert result.usage.model == MODEL
        assert result.provenance.source_ref == "prompt:classify@2"
        assert result.provenance.request_id == "resp_1"
        assert result.provenance.terms == "licensed_api"

    def test_thinking_tokens_count_as_output(self) -> None:
        body = answer("{}")
        body["usageMetadata"]["thoughtsTokenCount"] = 30
        result = adapter_with(FakeTransport(body)).generate_structured(PROMPT, ECHO)
        assert result.usage.output_tokens == 37

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
            return HttpResponse(200, {}, json.dumps(answer(next(replies))).encode())

        transport = FakeTransport(responder=responder)
        result = structured_call(adapter_with(transport), PROMPT, pydantic_schema(Out))
        assert result.data == Out(label="fit")
        assert len(transport.requests) == 2
        assert result.usage.input_tokens == 24
        assert result.usage.output_tokens == 14
        second = body_of(transport, 1)["contents"][0]["parts"][0]["text"]
        assert "label (string_type)" in second

    def test_parts_are_joined_and_thought_parts_are_ignored(self) -> None:
        body = answer("{}")
        body["candidates"][0]["content"]["parts"] = [
            {"text": "let me think", "thought": True},
            {"text": '{"b":'},
            {"text": " 2}"},
        ]
        assert adapter_with(FakeTransport(body)).generate_structured(PROMPT, ECHO).data == {"b": 2}

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            (answer("not json"), "json_invalid"),
            (answer("[1, 2]"), None),
            (
                {
                    **answer("{}"),
                    "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
                },
                "no_text",
            ),
            ({**answer("{}"), "candidates": [{"finishReason": "STOP"}]}, "no_text"),
            (
                {
                    **answer("{}"),
                    "candidates": [
                        {"content": {"parts": [{"text": "{}"}]}, "finishReason": "SAFETY"}
                    ],
                },
                "stop_safety",
            ),
            (
                {
                    **answer("{}"),
                    "candidates": [
                        {"content": {"parts": [{"text": "{"}]}, "finishReason": "MAX_TOKENS"}
                    ],
                },
                "stop_max_tokens",
            ),
            (
                {
                    "candidates": [],
                    "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
                    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7},
                },
                "blocked_prohibited_content",
            ),
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

    @pytest.mark.parametrize(
        "body",
        [
            None,
            [],
            {"candidates": "x"},
            {"candidates": []},
            {"candidates": ["x"]},
            {"candidates": [{"content": {"parts": "x"}}]},
        ],
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


class TestPrepareSchema:
    def test_unsupported_keywords_are_dropped_and_supported_ones_kept(self) -> None:
        schema = {
            "type": "object",
            "title": "Out",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 5, "pattern": "^a"},
                "score": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3},
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["a", "b"]},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                },
                "kind": {"const": "fixed"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        assert prepare_schema(schema) == {
            "type": "object",
            "title": "Out",
            "properties": {
                "name": {"type": "string"},
                "score": {"type": "integer", "minimum": 0, "maximum": 10},
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["a", "b"]},
                    "minItems": 1,
                    "maxItems": 3,
                },
                "kind": {"enum": ["fixed"]},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    def test_a_property_named_like_a_keyword_is_kept(self) -> None:
        schema = {"type": "object", "properties": {"pattern": {"type": "string"}}}
        assert prepare_schema(schema)["properties"] == {"pattern": {"type": "string"}}

    def test_ref_siblings_are_removed_and_defs_are_cleaned(self) -> None:
        schema = {
            "$defs": {"Item": {"type": "string", "maxLength": 3}},
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/Item", "description": "no siblings"}},
        }
        assert prepare_schema(schema) == {
            "$defs": {"Item": {"type": "string"}},
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/Item"}},
        }

    def test_the_input_is_not_changed(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"a": {"type": "string", "maxLength": 2}},
        }
        prepare_schema(schema)
        assert schema["properties"]["a"]["maxLength"] == 2


class TestConnection:
    def test_gets_the_configured_model_and_the_result_is_safe(self) -> None:
        transport = routed()
        result = adapter_with(transport).test_connection()
        assert result.ok
        request = transport.requests[0]
        assert (request.method, request.url) == (
            "GET",
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}",
        )
        assert request.body is None
        assert request.headers["x-goog-api-key"] == KEY
        assert GeminiAdapter.capabilities.test_connection_costs_credits is False

    def test_a_wrong_model_name_is_reported_as_not_found(self) -> None:
        result = adapter_with(FakeTransport(status=404)).test_connection()
        assert result.ok is False
        assert result.error_code == ProviderNotFound.code

    def test_failure_has_code_and_fixed_message(self) -> None:
        result = adapter_with(
            FakeTransport(status=403, body={"error": {"message": KEY}})
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
        props = GeminiAdapter.config_schema["properties"]
        assert "default" not in props["model"]
        assert "model" in GeminiAdapter.config_schema["required"]
        with pytest.raises(ProviderNotConfigured) as info:
            GeminiAdapter({}, {"api_key": KEY})
        assert "model" in info.value.fields
        assert not GeminiAdapter.validate_config({"model": "  "}, {"api_key": KEY}).ok

    def test_defaults(self) -> None:
        adapter = GeminiAdapter({"model": MODEL}, {"api_key": KEY})
        assert adapter.config["api_base"] == "https://generativelanguage.googleapis.com"
        assert adapter.config["structured_output"] == "response_json_schema"
        assert adapter.config["send_temperature"] is True
        assert adapter.config["timeout_seconds"] == 60
        assert adapter.config.get("thinking_budget") is None

    @pytest.mark.parametrize(
        "model",
        [
            "gemini 2.5",
            "gemini/../../x",
            "gemini-2.5-flash?key=x",
            "gemini-2.5-flash#frag",
            "models/",
            "-leading-dash",
            "a" * 101,
            "models/a/b",
        ],
    )
    def test_model_must_be_a_plain_id_because_it_goes_into_the_url(self, model: str) -> None:
        result = GeminiAdapter.validate_config({"model": model}, {"api_key": KEY})
        assert "model" in result.fields

    @pytest.mark.parametrize("model", [MODEL, f"models/{MODEL}", "gemini-3.0-pro-preview"])
    def test_ordinary_model_ids_are_accepted(self, model: str) -> None:
        assert GeminiAdapter.validate_config({"model": model}, {"api_key": KEY}).ok

    @pytest.mark.parametrize(
        "api_base",
        [
            "http://generativelanguage.googleapis.com",
            "https://evil.example.com",
            "https://generativelanguage.googleapis.com.evil.example.com",
            "https://user:" + "pw@generativelanguage.googleapis.com",
            "https://generativelanguage.googleapis.com:8443",
            "https://generativelanguage.googleapis.com/?x=1",
            "ftp://generativelanguage.googleapis.com",
            "https://169.254.169.254",
        ],
    )
    def test_api_base_must_be_the_allow_listed_https_host(self, api_base: str) -> None:
        result = GeminiAdapter.validate_config(
            {"model": MODEL, "api_base": api_base}, {"api_key": KEY}
        )
        assert ("api_base", "enum") in {(i.field, i.code) for i in result.issues}

    def test_structured_output_style_and_numbers_are_bounded(self) -> None:
        for config in (
            {"structured_output": "other"},
            {"timeout_seconds": 0},
            {"timeout_seconds": 301},
            {"thinking_budget": -1},
            {"thinking_budget": 40000},
            {"thinking_budget": 1.5},
        ):
            assert not GeminiAdapter.validate_config(
                {"model": MODEL, **config}, {"api_key": KEY}
            ).ok

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

        slots = [(d["category"], d["key"]) for d in default_registry.describe()]
        assert not any(GeminiAdapter.key == key for _, key in slots)
