"""Google Gemini adapter for structured LLM output.

Checked against Google's published API description (the v1beta discovery document at
``https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta``) on 2026-10-31:

* ``POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`` with
  the key in the ``x-goog-api-key`` header (also the form in Google's API-key guide).
* Body: ``contents`` (``[{"role": "user", "parts": [{"text": ...}]}]``), optional
  ``systemInstruction`` (text only), ``generationConfig`` with ``temperature`` (0.0 to 2.0; the
  spec says it is silently ignored on Gemini 3.6 Flash and newer), ``maxOutputTokens``,
  ``responseMimeType`` and the JSON-schema field, and optional ``thinkingConfig``.
* Structured output has two spellings in the spec, and Google is moving between them:
  ``generationConfig.responseMimeType = "application/json"`` plus ``generationConfig.
  responseJsonSchema`` (documented JSON Schema subset; marked deprecated), and the newer
  ``generationConfig.responseFormat.text = {"mimeType": "APPLICATION_JSON", "schema": ...}``.
  The setting ``structured_output`` picks one; the default is the long-standing first spelling.
  Either way the answer is JSON text in ``candidates[0].content.parts[].text`` and the full
  validation still runs on our side, so a schema the model only partly honours is caught.
* Response: ``candidates[].finishReason`` (``STOP``, ``MAX_TOKENS``, ``SAFETY``, ``RECITATION``,
  ``BLOCKLIST``, ``PROHIBITED_CONTENT``, ``SPII``, ``OTHER`` and more),
  ``promptFeedback.blockReason`` when the prompt itself was blocked, ``usageMetadata``
  (``promptTokenCount``, ``candidatesTokenCount``, ``thoughtsTokenCount``), ``modelVersion``
  and ``responseId``.
* ``GET /v1beta/models/{model}`` returns the model (used by ``test_connection``; it generates no
  tokens and also proves the configured model name exists).

NOT verified against a live key (none was available): the exact error JSON, whether every model
accepts both structured-output spellings, and the price per token. No model name is built in:
``model`` is a required setting. Thinking models count their thinking tokens against
``maxOutputTokens``, so asking for exactly the answer length can end in ``MAX_TOKENS`` with a
cut-off answer (seen live on ``gemini-2.5-flash``). ``maxOutputTokens`` is therefore the
requested ``max_tokens`` plus room for thinking: ``DEFAULT_THINKING_HEADROOM`` tokens, or the
``thinking_budget`` setting when set (0 turns thinking off on models that allow it, and adds no
room). The sum is capped at ``OUTPUT_TOKEN_CAP``.

The ``Transport`` is injectable; tests never use the network.
"""

from __future__ import annotations

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

API_HOST = "generativelanguage.googleapis.com"
DEFAULT_API_BASE = f"https://{API_HOST}"
API_VERSION_PATH = "/v1beta"

# Room left for thinking on top of the answer length (thinking tokens share maxOutputTokens), and
# the largest output limit the current models allow.
DEFAULT_THINKING_HEADROOM = 8192
OUTPUT_TOKEN_CAP = 65_536

STYLE_JSON_SCHEMA = "response_json_schema"
STYLE_RESPONSE_FORMAT = "response_format"

# A model id as the provider lists it ("gemini-2.5-flash"), optionally with the "models/"
# prefix. It goes into a URL path, so nothing else (no "?", "#", ".." or spaces) is accepted.
_MODEL_ID = re.compile(r"^(?:models/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# The JSON Schema keywords the structured-output endpoint documents as supported; everything
# else is removed from the schema we SEND only (our own validation enforces the rest).
_SUPPORTED_KEYWORDS = frozenset(
    {
        "$id",
        "$defs",
        "$ref",
        "$anchor",
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "properties",
        "additionalProperties",
        "required",
    }
)
_MAP_KEYWORDS = frozenset({"properties", "$defs"})
_OPAQUE_KEYWORDS = frozenset({"enum"})

# Finish reasons that mean "no usable answer", with the code recorded for each.
_BAD_FINISH = frozenset(
    {
        "MAX_TOKENS",
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "OTHER",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "MALFORMED_FUNCTION_CALL",
        "MALFORMED_RESPONSE",
        "UNEXPECTED_TOOL_CALL",
        "TOO_MANY_TOOL_CALLS",
        "ESCALATION",
        "PUP_LIMITED_DISABLED",
    }
)


def prepare_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``schema`` limited to the keywords Gemini documents as supported.

    ``const`` becomes a one-value ``enum``; ``$ref`` sub-schemas keep only ``$``-keys (the API
    rejects siblings); everything unsupported (string length limits, ``pattern``,
    ``uniqueItems``, ``default``, ``examples``, ...) is dropped. Full validation stays on our
    side."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return {k: walk(v) for k, v in node.items() if k.startswith("$")}
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "const":
                out.setdefault("enum", [value])
            elif key not in _SUPPORTED_KEYWORDS:
                continue
            elif key in _OPAQUE_KEYWORDS:
                out[key] = list(value) if isinstance(value, list) else value
            elif key in _MAP_KEYWORDS and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}
            elif key == "additionalProperties" and isinstance(value, bool):
                out[key] = value
            else:
                out[key] = walk(value)
        return out

    prepared: dict[str, Any] = walk(dict(schema))
    return prepared


def _model_path(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


class GeminiAdapter(LLMAdapter):
    """Gemini via ``generateContent`` with JSON-schema structured outputs."""

    key = "gemini"
    display_name = "Google Gemini"
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
                "maxLength": 110,
                "description": "Model id, exactly as the provider lists it. No default.",
            },
            "api_base": {
                "type": "string",
                "default": DEFAULT_API_BASE,
                "maxLength": 200,
                "description": "HTTPS base URL; the host must be on the allow list.",
            },
            "structured_output": {
                "type": "string",
                "enum": [STYLE_JSON_SCHEMA, STYLE_RESPONSE_FORMAT],
                "default": STYLE_JSON_SCHEMA,
                "description": "How the answer schema is sent (Google is moving to the second).",
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
                "default": True,
                "description": "Send the temperature (ignored by the newest models).",
            },
            "thinking_budget": {
                "type": "number",
                "minimum": 0,
                "maximum": 32768,
                "description": "Thinking tokens for models that think; 0 turns it off where "
                "allowed. Not sent when unset (answers then get default room to think).",
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
        thinking = config.get("thinking_budget")
        if (
            isinstance(thinking, (int, float))
            and not isinstance(thinking, bool)
            and thinking != int(thinking)
            and not any(i.field == "thinking_budget" for i in issues)
        ):
            issues.append(ConfigIssue("thinking_budget", "type"))
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
        return str(self.config["api_base"]).rstrip("/") + API_VERSION_PATH + path

    def _send(self, method: str, path: str, payload: Mapping[str, Any] | None) -> HttpResponse:
        headers = {
            "x-goog-api-key": self.credential("api_key").reveal(),
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
        """``GET /v1beta/models/{model}``: authenticates the key and proves the configured model
        exists, without generating tokens."""

        def probe() -> None:
            response = self._send("GET", "/" + _model_path(str(self.config["model"])), None)
            if not isinstance(self._json(response).get("name"), str):
                raise ProviderUpstreamError(provider_name=self.key)

        return self.timed_connection_test(probe)

    def _generation_config(
        self, schema: StructuredSchema[Any], temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        prepared = prepare_schema(schema.json_schema)
        budget = self.config.get("thinking_budget")
        headroom = DEFAULT_THINKING_HEADROOM if budget is None else int(budget)
        limit = max(max_tokens, min(max_tokens + headroom, OUTPUT_TOKEN_CAP))
        config: dict[str, Any] = {"maxOutputTokens": limit}
        if self.config["structured_output"] == STYLE_RESPONSE_FORMAT:
            config["responseFormat"] = {
                "text": {"mimeType": "APPLICATION_JSON", "schema": prepared}
            }
        else:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = prepared
        if self.config["send_temperature"]:
            config["temperature"] = temperature
        if budget is not None:
            config["thinkingConfig"] = {"thinkingBudget": int(budget)}
        return config

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
            "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
            "generationConfig": self._generation_config(schema, temperature, max_tokens),
        }
        if prompt.system:
            payload["systemInstruction"] = {"parts": [{"text": prompt.system}]}
        path = f"/{_model_path(str(self.config['model']))}:generateContent"
        response = self._send("POST", path, payload)
        data = self._json(response)
        usage = self._usage(data)
        data_obj = self._parse(data, schema, usage)
        response_id = data.get("responseId")
        return AdapterResult(
            data=data_obj,
            provenance=self.provenance(
                source_ref=f"prompt:{prompt.name}@{prompt.version}",
                request_id=response_id if isinstance(response_id, str) else None,
            ),
            usage=usage,
        )

    # ------------------------------------------------------------------ response handling
    def _usage(self, data: Mapping[str, Any]) -> Usage:
        raw = data.get("usageMetadata")
        usage = raw if isinstance(raw, Mapping) else {}
        tokens_in = usage.get("promptTokenCount")
        answer = usage.get("candidatesTokenCount")
        thoughts = usage.get("thoughtsTokenCount")
        tokens_out = None
        if isinstance(answer, int) and not isinstance(answer, bool):
            # Thinking tokens are billed as output, so they count as output here too.
            tokens_out = answer + (thoughts if isinstance(thoughts, int) else 0)
        credits = None
        if isinstance(tokens_in, int) and isinstance(tokens_out, int):
            # No price list is built in, so the provider's unit is "thousand tokens": a monthly
            # credit limit on the connection then caps usage without knowing the price.
            credits = Decimal(tokens_in + tokens_out) / 1000
        model = data.get("modelVersion")
        return Usage(
            credits=credits,
            input_tokens=tokens_in if isinstance(tokens_in, int) else None,
            output_tokens=tokens_out,
            model=model if isinstance(model, str) else str(self.config["model"]),
        )

    def _parse[T](self, data: Mapping[str, Any], schema: StructuredSchema[T], usage: Usage) -> T:
        def invalid(*issues: OutputIssue) -> StructuredOutputError:
            return StructuredOutputError(issues, provider_name=self.key, usage=usage)

        feedback = data.get("promptFeedback")
        block = feedback.get("blockReason") if isinstance(feedback, Mapping) else None
        candidates = data.get("candidates")
        if not candidates and isinstance(block, str) and block != "BLOCK_REASON_UNSPECIFIED":
            raise invalid(OutputIssue("", f"blocked_{block.lower()}"))
        if not isinstance(candidates, list) or not candidates:
            raise ProviderUpstreamError(provider_name=self.key)
        candidate = candidates[0]
        if not isinstance(candidate, Mapping):
            raise ProviderUpstreamError(provider_name=self.key)
        finish = candidate.get("finishReason")
        if finish in _BAD_FINISH:
            raise invalid(OutputIssue("", f"stop_{str(finish).lower()}"))
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        if parts is not None and not isinstance(parts, list):
            raise ProviderUpstreamError(provider_name=self.key)
        # Thinking summaries come back as parts flagged ``thought``; only the answer is used.
        texts = [
            p["text"]
            for p in (parts or [])
            if isinstance(p, Mapping) and isinstance(p.get("text"), str) and not p.get("thought")
        ]
        if not texts:
            raise invalid(OutputIssue("", "no_text"))
        try:
            decoded = json.loads("".join(texts))
        except ValueError:
            raise invalid(OutputIssue("", "json_invalid")) from None
        try:
            return schema.parse(decoded, provider_name=self.key)
        except ProviderInvalidOutput as exc:
            raise invalid(*getattr(exc, "issues", ())) from None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


__all__ = ["GeminiAdapter", "prepare_schema"]
