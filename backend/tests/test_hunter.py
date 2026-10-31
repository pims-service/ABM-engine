"""Hunter email finder: the adapter and the API flow, fake transport only."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from apps.integrations import services
from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.hunter import HunterAdapter
from apps.integrations.framework import (
    PersonRef,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
    ProviderUpstreamError,
)
from apps.integrations.testing import check_connection
from tests.adapter_contract import EmailEnrichmentContract
from tests.factories import (
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
)
from tests.test_company_api import api_for

pytestmark = pytest.mark.unit

KEY = "YOUR_API_KEY"  # pragma: allowlist secret
FOUND = {
    "data": {
        "first_name": "Sara",
        "last_name": "Haddad",
        "email": "sara@atlas.example.com",
        "score": 91,
        "domain": "atlas.example.com",
        "verification": {"status": "valid"},
    }
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


def routed() -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        body: Any = (
            {"data": {"email": "owner@hunter.example.com", "plan_name": "Free"}}
            if "/account" in request.url
            else {
                "data": {
                    "email": "alex@acme.example.com",
                    "score": 80,
                    "domain": "acme.example.com",
                }
            }
        )
        return HttpResponse(200, {}, json.dumps(body).encode())

    return FakeTransport(responder=responder)


def adapter_with(transport: FakeTransport, **config: Any) -> HunterAdapter:
    return HunterAdapter(config, {"api_key": KEY}, transport=transport)


SARA = PersonRef("Sara Haddad")


class TestHunterContract(EmailEnrichmentContract):
    adapter_cls = HunterAdapter

    def make_config(self) -> dict[str, Any]:
        return {}

    def make_adapter(self) -> Any:
        return self.adapter_cls(self.make_config(), self.make_credentials(), transport=routed())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        transport = FakeTransport(status=429, headers={"retry-after": "5"})
        return self.adapter_cls(self.make_config(), self.make_credentials(), transport=transport)


class TestFindEmail:
    def test_request_follows_the_documented_api(self) -> None:
        transport = FakeTransport(FOUND)
        adapter_with(transport).find_email(SARA, "atlas.example.com")
        request = transport.requests[0]
        assert request.method == "GET"
        assert request.url == (
            "https://api.hunter.io/v2/email-finder"
            "?domain=atlas.example.com&first_name=Sara&last_name=Haddad"
        )
        assert request.headers["x-api-key"] == KEY
        assert KEY not in request.url
        assert request.body is None
        assert request.timeout == 30

    def test_first_and_last_word_are_used_and_www_is_dropped(self) -> None:
        transport = FakeTransport(FOUND)
        adapter_with(transport).find_email(PersonRef("Sara Maria Haddad"), "WWW.Atlas.Example.com")
        assert transport.requests[0].url.endswith(
            "?domain=atlas.example.com&first_name=Sara&last_name=Haddad"
        )

    def test_a_hit_has_the_email_a_confidence_and_costs_one_credit(self) -> None:
        result = adapter_with(FakeTransport(FOUND)).find_email(SARA, "atlas.example.com")
        assert result.data is not None
        assert result.data.email == "sara@atlas.example.com"
        assert result.data.confidence == Decimal("0.91")
        assert result.data.status == "unknown"  # a finder never marks an email verified
        assert result.usage.credits == Decimal(1)
        assert result.provenance.source_ref == "email-finder:atlas.example.com"

    @pytest.mark.parametrize(
        "transport",
        [
            FakeTransport({"data": {"email": None, "score": None}}),
            FakeTransport({"data": {"email": "  "}}),
            FakeTransport(status=404),
            FakeTransport(status=451),
        ],
    )
    def test_nothing_found_is_a_normal_answer_that_costs_nothing(
        self, transport: FakeTransport
    ) -> None:
        result = adapter_with(transport).find_email(SARA, "atlas.example.com")
        assert result.data is not None
        assert result.data.email is None
        assert result.usage.credits == Decimal(0)

    def test_needs_a_first_and_last_name_and_a_real_domain_before_any_request(self) -> None:
        transport = FakeTransport(FOUND)
        adapter = adapter_with(transport)
        for person, domain in (
            (PersonRef("Sara"), "atlas.example.com"),
            (SARA, ""),
            (SARA, "not a domain"),
            (SARA, "atlas.example.com/path?x=1"),
            (SARA, "localhost"),
        ):
            with pytest.raises(ProviderBadRequest):
                adapter.find_email(person, domain)
        assert transport.requests == []

    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (400, ProviderBadRequest),
            (401, ProviderAuthError),
            (403, ProviderRateLimited),  # the docs call a 403 "the rate limit was reached"
            (429, ProviderRateLimited),
            (500, ProviderUnavailable),
        ],
    )
    def test_error_statuses_become_typed_errors(
        self, status: int, error: type[ProviderError]
    ) -> None:
        with pytest.raises(error):
            adapter_with(FakeTransport(status=status)).find_email(SARA, "atlas.example.com")

    @pytest.mark.parametrize("body", [[], {"nope": 1}, {"data": "x"}])
    def test_a_malformed_answer_is_an_upstream_error(self, body: Any) -> None:
        with pytest.raises(ProviderUpstreamError):
            adapter_with(FakeTransport(body)).find_email(SARA, "atlas.example.com")

    def test_a_page_that_is_not_json_is_an_upstream_error(self) -> None:
        transport = FakeTransport(responder=lambda r: HttpResponse(200, {}, b"<html>"))
        with pytest.raises(ProviderUpstreamError):
            adapter_with(transport).find_email(SARA, "atlas.example.com")


class TestConnection:
    def test_reads_the_free_account_endpoint(self) -> None:
        transport = routed()
        assert adapter_with(transport).test_connection().ok
        request = transport.requests[0]
        assert (request.method, request.url) == ("GET", "https://api.hunter.io/v2/account")
        assert request.headers["x-api-key"] == KEY
        assert HunterAdapter.capabilities.test_connection_costs_credits is False

    def test_a_rejected_key_fails_with_a_fixed_message(self) -> None:
        result = adapter_with(
            FakeTransport(status=401, body={"errors": [{"details": KEY}]})
        ).test_connection()
        assert result.ok is False
        assert result.error_code == "provider_auth"
        assert result.message == ProviderAuthError.message
        assert KEY not in repr(result.as_dict())

    def test_an_unexpected_success_body_fails(self) -> None:
        assert adapter_with(FakeTransport({"nope": 1})).test_connection().ok is False


class TestConfigAndSecrets:
    def test_defaults_and_no_model(self) -> None:
        adapter = HunterAdapter({}, {"api_key": KEY})
        assert adapter.config["api_base"] == "https://api.hunter.io/v2"
        assert adapter.config["timeout_seconds"] == 30
        assert "model" not in HunterAdapter.config_schema["properties"]

    @pytest.mark.parametrize(
        "api_base",
        [
            "http://api.hunter.io/v2",
            "https://evil.example.com/v2",
            "https://api.hunter.io.evil.example.com/v2",
            "https://user:" + "pw@api.hunter.io/v2",
            "https://api.hunter.io:8443/v2",
            "https://api.hunter.io/v2?x=1",
        ],
    )
    def test_api_base_must_be_the_allow_listed_https_host(self, api_base: str) -> None:
        result = HunterAdapter.validate_config({"api_base": api_base}, {"api_key": KEY})
        assert ("api_base", "enum") in {(i.field, i.code) for i in result.issues}

    def test_key_never_in_repr_logs_or_errors(self, caplog: pytest.LogCaptureFixture) -> None:
        transport = FakeTransport(status=401, body={"errors": KEY})
        adapter = adapter_with(transport)
        with caplog.at_level(logging.DEBUG), pytest.raises(ProviderAuthError) as info:
            adapter.find_email(SARA, "atlas.example.com")
        shown = " ".join([repr(adapter), str(info.value), repr(transport.requests[0]), caplog.text])
        assert KEY not in shown
        assert KEY not in repr(adapter.credential("api_key"))


# ------------------------------------------------------------------ the API flow
@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    shared = FakeTransport(FOUND)
    monkeypatch.setattr(
        "apps.integrations.adapters.hunter.adapter.UrllibTransport",
        lambda *args, **kwargs: shared,
    )
    return shared


@pytest.fixture
def world(fake: FakeTransport) -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    company = make_company(campaign=campaign, name="Atlas Freight")
    return {"client": client, "company": company, "api": api_for("manager", client)}


def connect_hunter(client: Any) -> None:
    services.create_connection(
        client,
        None,
        category="email_enrichment",
        provider="hunter",
        name="Hunter",
        config={},
        credentials={"api_key": KEY},
    )


@pytest.mark.django_db
def test_an_email_is_found_from_the_name_and_domain_without_a_profile_link(world, fake) -> None:
    connect_hunter(world["client"])
    make_human_decision(company=world["company"], decision="add")
    contact = make_contact(company=world["company"], name="Sara Haddad", email="")
    url = f"/api/v1/companies/{world['company'].pk}/find-email/"
    response = world["api"].post(url, {"contact_id": str(contact.pk)}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["email"] == "sara@atlas.example.com"
    assert response.json()["email_status"] == "unverified"
    assert f"domain={world['company'].domain}" in fake.requests[-1].url


@pytest.mark.django_db
def test_a_one_word_name_is_refused_with_a_reason(world) -> None:
    connect_hunter(world["client"])
    make_human_decision(company=world["company"], decision="add")
    contact = make_contact(company=world["company"], name="Sara", email="")
    url = f"/api/v1/companies/{world['company'].pk}/find-email/"
    response = world["api"].post(url, {"contact_id": str(contact.pk)}, format="json")
    assert response.status_code == 400
    assert "Hunter needs" in response.content.decode()


@pytest.mark.django_db
def test_without_a_connection_the_message_names_both_providers(world) -> None:
    make_human_decision(company=world["company"], decision="add")
    contact = make_contact(company=world["company"], name="Sara Haddad", email="")
    url = f"/api/v1/companies/{world['company'].pk}/find-email/"
    response = world["api"].post(url, {"contact_id": str(contact.pk)}, format="json")
    assert response.status_code == 400
    assert "SignalHire or Hunter" in response.content.decode()


@pytest.mark.django_db
def test_the_connection_test_works_for_hunter(world, fake) -> None:
    connect_hunter(world["client"])
    connection = services.resolve_connection(world["client"], "email_enrichment")
    assert connection is not None
    fake._fixed = HttpResponse(200, {}, json.dumps({"data": {"plan_name": "Free"}}).encode())
    assert check_connection(connection).ok
    assert fake.requests[-1].url.endswith("/account")
