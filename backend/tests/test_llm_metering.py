"""AI calls are metered, budget-capped and the key can be tested (no network)."""

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
from apps.integrations.usage import BudgetExceeded, set_budget
from tests.factories import make_campaign, make_client

pytestmark = [pytest.mark.django_db]

KEY = "YOUR_API_KEY"  # pragma: allowlist secret


class Answer(BaseModel):
    a: int


class FakeTransport:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.status != 200:
            return HttpResponse(self.status, {"request-id": "req_1"}, b"{}")
        if "/v1/models" in request.url:
            body: dict[str, Any] = {"data": []}
        else:
            body = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": '{"a": 1}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1500, "output_tokens": 500},
            }
        return HttpResponse(200, {"request-id": "req_1"}, json.dumps(body).encode())


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    fake = FakeTransport()
    monkeypatch.setattr(
        "apps.integrations.adapters.anthropic.adapter.UrllibTransport",
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
        provider="anthropic",
        name="Claude",
        config={"model": "claude-sonnet-5-5"},
        credentials={"api_key": KEY},
    )
    return {"client": client, "connection": connection, "campaign": make_campaign(client=client)}


def ask(adapter: Any) -> Any:
    prompt = PromptSpec(name="t", version="1", user="Say a=1")
    return structured_call(adapter, prompt, pydantic_schema(Answer), max_tokens=100)


def test_every_call_is_logged_with_tokens_and_the_campaign(transport, setup) -> None:
    adapter = get_llm_adapter(setup["client"].pk, campaign=setup["campaign"])
    assert ask(adapter).data == Answer(a=1)
    [call] = ProviderCall.objects.all()
    assert call.status == "ok"
    assert (call.input_tokens, call.output_tokens) == (1500, 500)
    assert call.model == "claude-sonnet-5-5"
    assert call.campaign_id == setup["campaign"].pk
    assert float(call.credits) == 2.0  # thousand tokens


def test_a_failed_call_is_logged_too(transport, setup) -> None:
    transport.status = 401
    adapter = get_llm_adapter(setup["client"].pk)
    with pytest.raises(ProviderAuthError):
        ask(adapter)
    [call] = ProviderCall.objects.all()
    assert call.status == "error"
    assert call.error_code


def test_the_monthly_budget_stops_calls_before_the_provider_is_contacted(transport, setup) -> None:
    set_budget(setup["connection"], None, credit_limit=3, hard_stop=True)
    adapter = get_llm_adapter(setup["client"].pk)
    ask(adapter)  # 2.0 of 3.0
    ask(adapter)  # 4.0 of 3.0
    sent = len(transport.requests)
    with pytest.raises(BudgetExceeded):
        ask(adapter)
    assert len(transport.requests) == sent  # nothing was sent
    assert ProviderCall.objects.filter(error_code="budget_exceeded").count() == 1


def test_no_connection_or_no_key_is_a_clear_error() -> None:
    client = make_client()
    with pytest.raises(LLMUnavailable, match="Add an Anthropic connection"):
        get_llm_adapter(client.pk)


def test_testing_the_key_works_for_anthropic(transport, setup) -> None:
    outcome = check_connection(setup["connection"])
    assert outcome.result == "ok"
    assert outcome.message == "The provider accepted the key."
    setup["connection"].refresh_from_db()
    assert setup["connection"].last_test_status == "ok"


def test_a_rejected_key_is_reported(transport, setup) -> None:
    transport.status = 401
    outcome = check_connection(setup["connection"])
    assert outcome.result == "failed"
    assert "credentials" in outcome.message.lower() or "key" in outcome.message.lower()


def test_a_connection_without_a_model_says_what_is_missing(transport) -> None:
    client = make_client()
    connection = services.create_connection(
        client,
        None,
        category="llm",
        provider="anthropic",
        name="NoModel",
        credentials={"api_key": KEY},
    )
    outcome = check_connection(connection)
    assert outcome.result == "failed"
    assert "model" in outcome.message
