"""Anthropic Messages API adapter for structured LLM output (issue #81).

Verified against the official Anthropic documentation on 2026-10-11 (retrieval date):

* Messages API: https://platform.claude.com/docs/en/api/messages
  ``POST https://api.anthropic.com/v1/messages`` with headers ``x-api-key``,
  ``anthropic-version: 2023-06-01`` and ``content-type: application/json``; body ``model``,
  ``max_tokens``, ``messages``, optional ``system``; response ``content`` blocks, ``model``,
  ``stop_reason`` and ``usage.input_tokens`` / ``usage.output_tokens``.
* Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
  generally available (no beta header) through ``output_config.format`` =
  ``{"type": "json_schema", "schema": {...}}``; the answer is JSON text in a ``text`` content
  block. Objects need ``additionalProperties: false``; numeric and string length constraints,
  recursive schemas and external ``$ref`` are not supported (``prepare_schema`` adapts the
  schema; our own validation still enforces the full Pydantic model). ``stop_reason`` of
  ``refusal`` or ``max_tokens`` can yield output that does not match the schema.
* Forced tool use (``tool_choice`` of type ``any``/``tool``) is rejected by the current models
  (https://platform.claude.com/docs/en/api/errors), so tool-use JSON is NOT used here.
* Errors and ``request-id``: https://platform.claude.com/docs/en/api/errors (400, 401, 402, 403,
  404, 409, 413, 429, 500, 504, 529; every response carries a ``request-id`` header).
* Models list: https://platform.claude.com/docs/en/api/models-list
  ``GET /v1/models?limit=1`` (used by ``test_connection``; it generates no tokens).
* Version header: https://platform.claude.com/docs/en/api/versioning (``2023-06-01``).

NOT verified (no current documentation found): the accepted range of ``temperature`` and
whether the newest models accept it at all. The reference page for the Messages API lists no
``temperature`` parameter, so it is sent only when the setting ``send_temperature`` is true.
Whether ``GET /v1/models`` is free of charge is assumed from it generating no tokens, not read
from a pricing statement. No model name is built in: ``model`` is a required setting.

The ``Transport`` is injectable; tests never use the network.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

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

from .transport import HttpRequest, HttpResponse, Transport, UrllibTransport

API_HOST = "api.anthropic.com"
DEFAULT_API_BASE = f"https://{API_HOST}"
DEFAULT_API_VERSION = "2023-06-01"
MESSAGES_PATH = "/v1/messages"
MODELS_PATH = "/v1/models?limit=1"

# Keywords the structured-output docs list as unsupported; removed from the schema we SEND only.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "maxItems",
        "uniqueItems",
    }
)
_OPAQUE_KEYWORDS = frozenset({"enum", "const", "default", "examples"})
_MAP_KEYWORDS = frozenset({"properties", "$defs", "definitions", "patternProperties"})


def prepare_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``schema`` that the structured-outputs endpoint accepts: every object gets
    ``additionalProperties: false`` and the unsupported constraint keywords are dropped
    (``minItems`` above 1 too). Validation of the full constraints stays on our side."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_KEYWORDS:
                continue
            if key == "minItems" and isinstance(value, int) and value > 1:
                continue
            if key in _OPAQUE_KEYWORDS:
                out[key] = copy.deepcopy(value)
            elif key in _MAP_KEYWORDS and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}
            else:
                out[key] = walk(value)
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
        return out

    prepared: dict[str, Any] = walk(dict(schema))
    return prepared


class AnthropicAdapter(LLMAdapter):
    """Claude via the Messages API with native JSON-schema structured outputs."""

    key = "anthropic"
    display_name = "Anthropic Claude"
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
            "anthropic_version": {
                "type": "string",
                "default": DEFAULT_API_VERSION,
                "maxLength": 20,
                "description": "Value of the anthropic-version request header.",
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
                "description": "Send the temperature parameter (not verified for all models).",
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
        if (
            isinstance(model, str)
            and not model.strip()
            and not any(i.field == "model" for i in issues)
        ):
            issues.append(ConfigIssue("model", "required"))
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
            "x-api-key": self.credential("api_key").reveal(),
            "anthropic-version": str(self.config["anthropic_version"]),
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
        """``GET /v1/models?limit=1``: authenticates the key without generating tokens. It does
        not check that the configured model exists."""

        def probe() -> None:
            response = self._send("GET", MODELS_PATH, None)
            if not isinstance(self._json(response).get("data"), list):
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
        payload: dict[str, Any] = {
            "model": str(self.config["model"]),
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt.user}],
            "output_config": {
                "format": {"type": "json_schema", "schema": prepare_schema(schema.json_schema)}
            },
        }
        if prompt.system:
            payload["system"] = prompt.system
        if self.config["send_temperature"]:
            payload["temperature"] = temperature
        response = self._send("POST", MESSAGES_PATH, payload)
        data = self._json(response)
        usage = self._usage(data)
        data_obj = self._parse(data, schema, usage)
        return AdapterResult(
            data=data_obj,
            provenance=self.provenance(
                source_ref=f"prompt:{prompt.name}@{prompt.version}",
                request_id=response.headers.get("request-id"),
            ),
            usage=usage,
        )

    # ------------------------------------------------------------------ response handling
    def _usage(self, data: Mapping[str, Any]) -> Usage:
        raw = data.get("usage")
        usage = raw if isinstance(raw, Mapping) else {}
        model = data.get("model")
        tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
        credits = None
        if isinstance(tokens_in, int) and isinstance(tokens_out, int):
            # No price list is built in, so the provider's unit is "thousand tokens": a monthly
            # credit limit on the connection then caps usage without knowing the price.
            credits = Decimal(tokens_in + tokens_out) / 1000
        return Usage(
            credits=credits,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            model=model if isinstance(model, str) else str(self.config["model"]),
        )

    def _parse[T](self, data: Mapping[str, Any], schema: StructuredSchema[T], usage: Usage) -> T:
        def invalid(*issues: OutputIssue) -> StructuredOutputError:
            return StructuredOutputError(issues, provider_name=self.key, usage=usage)

        if data.get("stop_reason") in ("refusal", "max_tokens"):
            raise invalid(OutputIssue("", f"stop_{data['stop_reason']}"))
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderUpstreamError(provider_name=self.key)
        text = next(
            (
                b["text"]
                for b in blocks
                if isinstance(b, Mapping)
                and b.get("type") == "text"
                and isinstance(b.get("text"), str)
            ),
            None,
        )
        if text is None:
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


__all__ = ["AnthropicAdapter", "prepare_schema"]
