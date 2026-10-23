"""ZeroBounce verification and the approved-account steps shown on the card."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import pytest

from apps.integrations import services
from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.zerobounce import ZeroBounceAdapter
from apps.integrations.framework import ProviderQuotaExceeded
from apps.integrations.testing import check_connection
from tests.factories import (
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
)
from tests.test_company_api import api_for

KEY = "YOUR_API_KEY"  # pragma: allowlist secret


class FakeZeroBounce:
    def __init__(self, answer: dict[str, Any] | None = None, credits: int = 100) -> None:
        self.answer = answer or {"status": "valid", "sub_status": "", "mx_found": "true"}
        self.credits = credits
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.url.endswith("/v2/getcredits"):
            body: Any = {"Credits": self.credits}
        else:
            body = self.answer
        return HttpResponse(200, {}, json.dumps(body).encode())

    def form(self) -> dict[str, list[str]]:
        return parse_qs((self.requests[-1].body or b"").decode())


def adapter(fake: FakeZeroBounce, **config: Any) -> ZeroBounceAdapter:
    return ZeroBounceAdapter(config, {"api_key": KEY}, transport=fake)


@pytest.mark.unit
def test_the_key_goes_in_the_body_never_in_the_url() -> None:
    fake = FakeZeroBounce()
    result = adapter(fake).verify("sara@atlas.example.com")
    request = fake.requests[0]
    assert request.method == "POST"
    assert KEY not in request.url
    assert "?" not in request.url
    assert fake.form()["email"] == ["sara@atlas.example.com"]
    assert fake.form()["api_key"] == [KEY]
    assert result.data is not None
    assert result.data.status == "valid"
    assert result.usage.credits == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("answer", "status"),
    [
        ({"status": "invalid", "sub_status": "mailbox_not_found"}, "invalid"),
        ({"status": "catch-all"}, "catch_all"),
        ({"status": "do_not_mail", "sub_status": "disposable"}, "disposable"),
        ({"status": "do_not_mail", "sub_status": "role_based"}, "risky"),
        ({"status": "spamtrap"}, "invalid"),
        ({"status": "abuse"}, "risky"),
        ({"status": "unknown", "sub_status": "greylisted"}, "unknown"),
        ({"status": "something new"}, "unknown"),
    ],
)
def test_only_a_clean_valid_is_valid(answer: dict[str, Any], status: str) -> None:
    result = adapter(FakeZeroBounce(answer)).verify("a@b.example.com")
    assert result.data is not None
    assert result.data.status == status
    if status == "unknown":
        assert result.usage.credits == 0


@pytest.mark.unit
def test_an_error_answer_is_a_quota_problem_and_a_bad_key_fails_the_test() -> None:
    with pytest.raises(ProviderQuotaExceeded):
        adapter(FakeZeroBounce({"error": "Invalid API Key or your account ran out"})).verify(
            "a@b.example.com"
        )
    result = adapter(FakeZeroBounce(credits=-1)).test_connection()
    assert not result.ok


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeZeroBounce:
    shared = FakeZeroBounce()
    monkeypatch.setattr(
        "apps.integrations.adapters.zerobounce.adapter.UrllibTransport",
        lambda *args, **kwargs: shared,
    )
    return shared


@pytest.fixture
def world(fake: FakeZeroBounce) -> dict[str, Any]:
    client = make_client()
    company = make_company(campaign=make_campaign(client=client))
    return {"client": client, "company": company, "api": api_for("manager", client)}


def connect(client: Any) -> Any:
    return services.create_connection(
        client,
        None,
        category="email_verification",
        provider="zerobounce",
        name="ZeroBounce",
        config={},
        credentials={"api_key": KEY},
    )


@pytest.mark.django_db
def test_a_valid_answer_marks_the_email_verified_for_an_approved_account(world) -> None:
    connect(world["client"])
    contact = make_contact(company=world["company"], email="sara@atlas.example.com")
    url = f"/api/v1/companies/{world['company'].pk}/verify-email/"
    body = {"contact_id": str(contact.pk)}
    assert world["api"].post(url, body, format="json").status_code == 400  # not approved yet
    make_human_decision(company=world["company"], decision="add")
    response = world["api"].post(url, body, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["email_status"] == "verified"


@pytest.mark.django_db
def test_an_uncertain_answer_leaves_the_email_unverified(world, fake) -> None:
    connect(world["client"])
    fake.answer = {"status": "catch-all"}
    make_human_decision(company=world["company"], decision="add")
    contact = make_contact(company=world["company"], email="sara@atlas.example.com")
    url = f"/api/v1/companies/{world['company'].pk}/verify-email/"
    response = world["api"].post(url, {"contact_id": str(contact.pk)}, format="json")
    assert response.json()["email_status"] == "unverified"


@pytest.mark.django_db
def test_the_connection_test_works(world, fake) -> None:
    connection = connect(world["client"])
    assert check_connection(connection).ok


@pytest.mark.django_db
def test_the_card_lists_the_steps_after_add_and_ticks_them_off(world) -> None:
    company = world["company"]
    url = f"/api/v1/companies/{company.pk}/card/"
    assert world["api"].get(url).json()["workflow"] == []
    make_human_decision(company=company, decision="add")
    steps = {s["key"]: s["done"] for s in world["api"].get(url).json()["workflow"]}
    assert steps == dict.fromkeys(
        ["contact", "email", "verify", "messages", "approve", "export"], False
    )
    make_contact(
        company=company, role="primary", email="sara@atlas.example.com", email_status="verified"
    )
    steps = {s["key"]: s["done"] for s in world["api"].get(url).json()["workflow"]}
    assert (steps["contact"], steps["email"], steps["verify"]) == (True, True, True)
    assert steps["messages"] is False
