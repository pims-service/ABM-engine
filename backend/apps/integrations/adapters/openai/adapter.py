"""OpenAI Chat Completions adapter for structured LLM output.

Checked against OpenAI's published OpenAPI description
(``github.com/openai/openai-openapi``, ``openapi.yaml``) on 2026-10-31:

* ``POST https://api.openai.com/v1/chat/completions`` with ``Authorization: Bearer <key>``.
* Body: ``model``, ``messages`` (``system`` and ``user`` roles), ``max_completion_tokens`` (the
  cap on visible output tokens PLUS reasoning tokens; ``max_tokens`` is deprecated and not
  accepted by reasoning models), optional ``temperature`` (0 to 2) and ``response_format`` =
  ``{"type": "json_schema", "json_schema": {"name", "schema", "strict"}}``. ``name`` is limited
  to letters, digits, underscores and dashes, 64 characters at most.
* ``strict`` is sent as ``false``: strict mode only accepts a subset of JSON Schema (every
  property required, no optional fields) that our own models do not follow. The schema then
  guides the model, and the full validation runs on our side (with one repair round trip).
* Response: ``choices[0].message.content`` (the JSON text) or ``message.refusal``,
  ``choices[0].finish_reason`` (``stop``, ``length``, ``content_filter`` ...), ``usage``
  (``prompt_tokens``; ``completion_tokens``, which already includes reasoning tokens) and the
  ``x-request-id`` response header.
* ``GET /v1/models/{model}`` returns the model (used by ``test_connection``; it generates no
  tokens and also proves the configured model name exists).

NOT verified against a live key (none was available): the exact error JSON, which models accept
``temperature`` (reasoning models do not, so ``send_temperature`` is off by default), and the
output limit of each model. Reasoning models count thinking against ``max_completion_tokens``,
so the limit sent is the requested ``max_tokens`` plus ``output_headroom`` (default
``DEFAULT_OUTPUT_HEADROOM``; 0 sends exactly ``max_tokens``), capped at ``OUTPUT_TOKEN_CAP``,
a value every current model accepts. No model name is built in: ``model`` is a required
setting.

The ``Transport`` is injectable; tests never use the network.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

from apps.integrations.adapters.anthropic.transport import (
    HttpRequest,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from apps.integrations.framework import (
    AdapterCapabilities,
    AdapterResult,
    ConfigIssue,
    ConfigValidation,
    ConnectionTestResult,
    LLMAdapter,
    PromptSpec,
    ProviderBadRequest,
    ProviderInvalidOutput,
    ProviderUpstreamError,
    SecretStr,
    StructuredSchema,
    Usage,
    map_http_status,
)
from apps.integrations.framework.categories import STRUCTURED_OUTPUT
from apps.integrations.llm.errors import OutputIssue, StructuredOutputError

API_HOST = "api.openai.com"
DEFAULT_API_BASE = f"https://{API_HOST}"
CHAT_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"

# Room left for reasoning on top of the answer length, and the largest output limit sent (the
# smallest maximum among current chat models, so the request is never refused for size).
DEFAULT_OUTPUT_HEADROOM = 8192
OUTPUT_TOKEN_CAP = 16_384

# A model id as the provider lists it ("gpt-4o", "o3", "ft:gpt-4o:org::abc123"). It goes into a
# URL path, so nothing else (no "/", "?", "#", ".." or spaces) is accepted.
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_SCHEMA_NAME = re.compile(r"[^A-Za-z0-9_-]")


def schema_name(name: str) -> str:
    """``name`` as the API accepts it for ``json_schema.name``."""
    return _SCHEMA_NAME.sub("_", name)[:64] or "answer"


class OpenAIAdapter(LLMAdapter):
    """OpenAI models via Chat Completions with JSON-schema structured outputs."""

    key = "openai"
    display_name = "OpenAI"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset({STRUCTURED_OUTPUT}),
        requires_credentials=True,
        allowed_hosts=(API_HOST,),
        test_connection_costs_credits=False,
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "maxLength": 100,
                "description": "Model id, exactly as the provider lists it. No default.",
            },
            "api_base": {
                "type": "string",
                "default": DEFAULT_API_BASE,
                "maxLength": 200,
                "description": "HTTPS base URL; the host must be on the allow list.",
            },
            "timeout_seconds": {
                "type": "number",
                "default": 60,
                "minimum": 1,
                "maximum": 300,
                "description": "Connect and read timeout per request.",
            },
            "send_temperature": {
                "type": "boolean",
                "default": False,
                "description": "Send the temperature (reasoning models do not accept it).",
            },
            "output_headroom": {
                "type": "number",
                "default": DEFAULT_OUTPUT_HEADROOM,
                "minimum": 0,
                "maximum": OUTPUT_TOKEN_CAP,
                "description": "Extra output tokens for models that reason; 0 sends exactly "
                "the requested answer length.",
            },
        },
        "required": ["model"],
        "additionalProperties": False,
        "x-credentials": {"required": ["api_key"], "optional": []},
    }

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(config, credentials, clock=clock)
        self._transport: Transport = transport or UrllibTransport(
            self.capabilities.allowed_hosts, provider_name=self.key
        )

    @classmethod
    def validate_config(
        cls, config: Mapping[str, Any], credentials: Mapping[str, SecretStr | str] | None = None
    ) -> ConfigValidation:
        base = super().validate_config(config, credentials)
        issues = list(base.issues)
        model = config.get("model")
        if isinstance(model, str) and not any(i.field == "model" for i in issues):
            if not model.strip():
                issues.append(ConfigIssue("model", "required"))
            elif not _MODEL_ID.match(model):
                issues.append(ConfigIssue("model", "enum"))
        headroom = config.get("output_headroom")
        if (
            isinstance(headroom, (int, float))
            and not isinstance(headroom, bool)
            and headroom != int(headroom)
            and not any(i.field == "output_headroom" for i in issues)
        ):
            issues.append(ConfigIssue("output_headroom", "type"))
        api_base = config.get("api_base")
        if isinstance(api_base, str) and not any(i.field == "api_base" for i in issues):
            parts = urlsplit(api_base)
            try:
                port = parts.port
            except ValueError:
                port = -1
            if (
                parts.scheme != "https"
                or (parts.hostname or "").lower() not in cls.capabilities.allowed_hosts
                or parts.username is not None
                or port not in (None, 443)
                or parts.query
                or parts.fragment
            ):
                issues.append(ConfigIssue("api_base", "enum"))
        return ConfigValidation(tuple(issues))

    # ------------------------------------------------------------------ requests
    def _url(self, path: str) -> str:
        return str(self.config["api_base"]).rstrip("/") + path

    def _send(self, method: str, path: str, payload: Mapping[str, Any] | None) -> HttpResponse:
        headers = {
            "authorization": f"Bearer {self.credential('api_key').reveal()}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        request = HttpRequest(
            method, self._url(path), headers, body, timeout=float(self.config["timeout_seconds"])
        )
        response = self._transport.send(request)
        if not 200 <= response.status < 300:
            raise map_http_status(
                response.status,
                provider_name=self.key,
                retry_after=response.headers.get("retry-after"),
            )
        return response

    def _json(self, response: HttpResponse) -> dict[str, Any]:
        try:
            decoded = json.loads(response.body)
        except ValueError:
            raise ProviderUpstreamError(provider_name=self.key) from None
        if not isinstance(decoded, dict):
            raise ProviderUpstreamError(provider_name=self.key)
        return decoded

    # ------------------------------------------------------------------ operations
    def test_connection(self) -> ConnectionTestResult:
        """``GET /v1/models/{model}``: authenticates the key and proves the configured model
        exists, without generating tokens."""

        def probe() -> None:
            response = self._send("GET", f"{MODELS_PATH}/{self.config['model']}", None)
            if not isinstance(self._json(response).get("id"), str):
                raise ProviderUpstreamError(provider_name=self.key)

        return self.timed_connection_test(probe)

    def generate_structured[T](
        self,
        prompt: PromptSpec,
        schema: StructuredSchema[T],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AdapterResult[T]:
        if not 0.0 <= temperature <= 1.0:
            raise ProviderBadRequest("temperature", provider_name=self.key)
        if not 1 <= max_tokens <= 200_000:
            raise ProviderBadRequest("max_tokens", provider_name=self.key)
        headroom = int(self.config["output_headroom"])
        limit = max(max_tokens, min(max_tokens + headroom, OUTPUT_TOKEN_CAP))
        messages: list[dict[str, str]] = []
        if prompt.system:
            messages.append({"role": "system", "content": prompt.system})
        messages.append({"role": "user", "content": prompt.user})
        payload: dict[str, Any] = {
            "model": str(self.config["model"]),
            "messages": messages,
            "max_completion_tokens": limit,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name(schema.name),
                    "schema": copy.deepcopy(dict(schema.json_schema)),
                    "strict": False,
                },
            },
        }
        if self.config["send_temperature"]:
            payload["temperature"] = temperature
        response = self._send("POST", CHAT_PATH, payload)
        data = self._json(response)
        usage = self._usage(data)
        data_obj = self._parse(data, schema, usage)
        request_id = response.headers.get("x-request-id") or data.get("id")
        return AdapterResult(
            data=data_obj,
            provenance=self.provenance(
                source_ref=f"prompt:{prompt.name}@{prompt.version}",
                request_id=request_id if isinstance(request_id, str) else None,
            ),
            usage=usage,
        )

    # ------------------------------------------------------------------ response handling
    def _usage(self, data: Mapping[str, Any]) -> Usage:
        raw = data.get("usage")
        usage = raw if isinstance(raw, Mapping) else {}
        tokens_in, tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        tokens_in = (
            tokens_in if isinstance(tokens_in, int) and not isinstance(tokens_in, bool) else None
        )
        tokens_out = (
            tokens_out if isinstance(tokens_out, int) and not isinstance(tokens_out, bool) else None
        )
        credits = None
        if tokens_in is not None and tokens_out is not None:
            # No price list is built in, so the provider's unit is "thousand tokens": a monthly
            # credit limit on the connection then caps usage without knowing the price.
            credits = Decimal(tokens_in + tokens_out) / 1000
        model = data.get("model")
        return Usage(
            credits=credits,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            model=model if isinstance(model, str) else str(self.config["model"]),
        )

    def _parse[T](self, data: Mapping[str, Any], schema: StructuredSchema[T], usage: Usage) -> T:
        def invalid(*issues: OutputIssue) -> StructuredOutputError:
            return StructuredOutputError(issues, provider_name=self.key, usage=usage)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderUpstreamError(provider_name=self.key)
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish in ("length", "content_filter"):
            raise invalid(OutputIssue("", f"stop_{finish}"))
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderUpstreamError(provider_name=self.key)
        if isinstance(message.get("refusal"), str) and message["refusal"].strip():
            raise invalid(OutputIssue("", "stop_refusal"))
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise invalid(OutputIssue("", "no_text"))
        try:
            decoded = json.loads(text)
        except ValueError:
            raise invalid(OutputIssue("", "json_invalid")) from None
        try:
            return schema.parse(decoded, provider_name=self.key)
        except ProviderInvalidOutput as exc:
            raise invalid(*getattr(exc, "issues", ())) from None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


__all__ = ["OpenAIAdapter", "schema_name"]
