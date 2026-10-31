"""A client's Gemini connection works end to end: metered and testable (no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from apps.integrations import services
from apps.integrations.adapters.anthropic import HttpRequest, HttpResponse
from apps.integrations.framework import PromptSpec, ProviderAuthError
from apps.integrations.llm import pydantic_schema, structured_call
from apps.integrations.llm_connection import LLMUnavailable, get_llm_adapter
from apps.integrations.models import ProviderCall
from apps.integrations.testing import check_connection
from tests.factories import make_campaign, make_client

pytestmark = [pytest.mark.django_db]

KEY = "YOUR_API_KEY"  # pragma: allowlist secret
MODEL = "gemini-2.5-flash"


class Answer(BaseModel):
    a: int


class FakeTransport:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.status != 200:
            return HttpResponse(self.status, {}, b"{}")
        if request.method == "GET":
            body: dict[str, Any] = {"name": f"models/{MODEL}"}
        else:
            body = {
                "candidates": [
                    {"content": {"parts": [{"text": '{"a": 1}'}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {
                    "promptTokenCount": 1500,
                    "candidatesTokenCount": 400,
                    "thoughtsTokenCount": 100,
                },
                "modelVersion": MODEL,
            }
        return HttpResponse(200, {}, json.dumps(body).encode())


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    fake = FakeTransport()
    monkeypatch.setattr(
        "apps.integrations.adapters.gemini.adapter.UrllibTransport",
        lambda *args, **kwargs: fake,
    )
    return fake


@pytest.fixture
def setup() -> dict[str, Any]:
    client = make_client()
    connection = services.create_connection(
        client,
        None,
        category="llm",
        provider="gemini",
        name="Gemini",
        config={"model": MODEL},
        credentials={"api_key": KEY},
    )
    return {"client": client, "connection": connection, "campaign": make_campaign(client=client)}


def ask(adapter: Any) -> Any:
    prompt = PromptSpec(name="t", version="1", user="Say a=1")
    return structured_call(adapter, prompt, pydantic_schema(Answer), max_tokens=100)


def test_a_gemini_connection_is_used_and_every_call_is_logged(transport, setup) -> None:
    adapter = get_llm_adapter(setup["client"].pk, campaign=setup["campaign"])
    assert ask(adapter).data == Answer(a=1)
    [call] = ProviderCall.objects.all()
    assert call.status == "ok"
    assert (call.input_tokens, call.output_tokens) == (1500, 500)  # thinking counts as output
    assert call.model == MODEL
    assert call.campaign_id == setup["campaign"].pk
    assert float(call.credits) == 2.0  # thousand tokens
    assert transport.requests[0].headers["x-goog-api-key"] == KEY


def test_a_failed_gemini_call_is_logged_too(transport, setup) -> None:
    transport.status = 403
    with pytest.raises(ProviderAuthError):
        ask(get_llm_adapter(setup["client"].pk))
    [call] = ProviderCall.objects.all()
    assert call.status == "error"
    assert call.error_code


def test_testing_the_gemini_key_also_checks_the_model(transport, setup) -> None:
    outcome = check_connection(setup["connection"])
    assert outcome.result == "ok"
    assert transport.requests[0].url.endswith(f"/v1beta/models/{MODEL}")
    setup["connection"].refresh_from_db()
    assert setup["connection"].last_test_status == "ok"


def test_a_wrong_model_name_fails_the_test(transport, setup) -> None:
    transport.status = 404
    assert check_connection(setup["connection"]).result == "failed"


def test_a_missing_model_setting_is_a_clear_error() -> None:
    client = make_client()
    services.create_connection(
        client,
        None,
        category="llm",
        provider="gemini",
        name="Gemini",
        config={},
        credentials={"api_key": KEY},
    )
    with pytest.raises(LLMUnavailable, match="not set up correctly"):
        get_llm_adapter(client.pk)


def test_an_unknown_provider_names_both_supported_ones() -> None:
    client = make_client()
    services.create_connection(
        client,
        None,
        category="llm",
        provider="mistral",
        name="Other",
        config={},
        credentials={"api_key": KEY},
    )
    with pytest.raises(LLMUnavailable, match="Anthropic, Gemini or OpenAI"):
        get_llm_adapter(client.pk)
