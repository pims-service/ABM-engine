"""SignalHire people search and email reveal: the adapters and the API flow, fake transport only."""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.companies.models import Contact
from apps.integrations import services
from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.signalhire import SignalHireEmailAdapter, SignalHirePeopleAdapter
from apps.integrations.framework import (
    CompanyRef,
    PersonRef,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
)
from apps.integrations.testing import check_connection
from tests.factories import (
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
)
from tests.test_company_api import api_for

TOKEN = "YOUR_API_KEY"  # pragma: allowlist secret
UID = "a" * 32
LINKEDIN = "https://www.linkedin.com/in/sara-haddad"

PROFILES = [
    {
        "uid": UID,
        "fullName": "Sara Haddad",
        "location": "Riyadh",
        "experience": [{"company": "Atlas Freight Co.", "title": "Head of BD"}],
    },
    {
        "uid": "b" * 32,
        "fullName": "Other Person",
        "location": "Dubai",
        "experience": [{"company": "Something Else Ltd", "title": "Sales Director"}],
    },
]


class FakeSignalHire:
    def __init__(self, *, status: int = 200, reveal: dict[str, Any] | None = None) -> None:
        self.status = status
        self.reveal = reveal
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.status != 200:
            return HttpResponse(self.status, {}, b"{}")
        if request.url.endswith("/credits"):
            body: Any = {"credits": 27}
        elif request.url.endswith("/candidate/searchByQuery"):
            body = {"requestId": 7, "total": 2, "profiles": PROFILES}
        else:
            body = [self.reveal or {"item": "x", "status": "failed"}]
        return HttpResponse(200, {}, json.dumps(body).encode())

    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].body or b"{}")  # type: ignore[no-any-return]


def people(fake: FakeSignalHire) -> SignalHirePeopleAdapter:
    return SignalHirePeopleAdapter({}, {"api_key": TOKEN}, transport=fake)


def emails(fake: FakeSignalHire) -> SignalHireEmailAdapter:
    return SignalHireEmailAdapter({}, {"api_key": TOKEN}, transport=fake)


REVEALED = {
    "item": UID,
    "status": "success",
    "candidate": {
        "fullName": "Sara Haddad",
        "contacts": [
            {
                "type": "email",
                "value": "sara.personal@example.com",
                "rating": 70,
                "subType": "personal",
            },
            {"type": "email", "value": "sara@atlas.example.com", "rating": 100, "subType": "work"},
            {"type": "phone", "value": "+966 5", "rating": 100, "subType": "mobile"},
        ],
    },
}


# ------------------------------------------------------------------ the adapters (no database)


@pytest.mark.unit
def test_the_search_asks_for_the_company_and_the_titles_and_drops_other_companies() -> None:
    fake = FakeSignalHire()
    result = people(fake).find_people(
        CompanyRef(name="Atlas Freight"), ["Head of BD", "CEO"], limit=5
    )
    sent = fake.body()
    assert sent["currentCompany"] == '"Atlas Freight"'
    assert sent["currentTitle"] == '("Head of BD" OR "CEO")'
    assert sent["size"] == 5
    assert fake.requests[0].headers["apikey"] == TOKEN
    assert result.data is not None
    assert [(p.name, p.title, p.provider_id) for p in result.data] == [
        ("Sara Haddad", "Head of BD", UID)
    ]
    assert "1 profiles were left out" in result.warnings[0]


@pytest.mark.unit
def test_a_search_needs_a_company_name() -> None:
    with pytest.raises(ProviderBadRequest):
        people(FakeSignalHire()).find_people(CompanyRef(domain="atlas.example.com"), ["CEO"])


@pytest.mark.unit
def test_the_work_email_is_picked_with_its_rating_and_costs_one_credit() -> None:
    fake = FakeSignalHire(reveal=REVEALED)
    result = emails(fake).find_email_by_uid(UID)
    assert result.data is not None
    assert result.data.email == "sara@atlas.example.com"
    assert result.data.status == "unknown"  # never "verified" by a finder
    assert str(result.data.confidence) == "1"
    assert result.usage.credits == 1
    assert fake.body() == {"items": [UID], "withoutWaterfall": True}


@pytest.mark.unit
def test_no_email_found_is_a_normal_answer_that_costs_nothing() -> None:
    result = emails(FakeSignalHire()).find_email(PersonRef("Sara", profile_url=LINKEDIN), "x.com")
    assert result.data is not None
    assert result.data.email is None
    assert result.usage.credits == 0


@pytest.mark.unit
def test_a_link_that_is_not_linkedin_is_refused() -> None:
    with pytest.raises(ProviderBadRequest):
        emails(FakeSignalHire()).find_email(
            PersonRef("Sara", profile_url="https://example.com/sara"), "x.com"
        )


@pytest.mark.unit
def test_out_of_credits_and_bad_keys_become_typed_errors() -> None:
    with pytest.raises(ProviderQuotaExceeded):
        people(FakeSignalHire(status=402)).find_people(CompanyRef(name="A"), ["CEO"])
    with pytest.raises(ProviderAuthError):
        emails(FakeSignalHire(status=401)).find_email_by_uid(UID)
    credits_over = {"item": UID, "status": "credits_are_over"}
    with pytest.raises(ProviderQuotaExceeded):
        emails(FakeSignalHire(reveal=credits_over)).find_email_by_uid(UID)


@pytest.mark.unit
def test_the_host_cannot_be_changed() -> None:
    with pytest.raises(ProviderNotConfigured):
        SignalHirePeopleAdapter({"api_base": "https://evil.example.com/api/v1"}, {"api_key": TOKEN})


# ------------------------------------------------------------------ the API flow

pytestmark_db = pytest.mark.django_db


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSignalHire:
    shared = FakeSignalHire(reveal=REVEALED)
    monkeypatch.setattr(
        "apps.integrations.adapters.signalhire.adapter.UrllibTransport",
        lambda *args, **kwargs: shared,
    )
    return shared


def connect(client: Any, category: str) -> None:
    services.create_connection(
        client,
        None,
        category=category,
        provider="signalhire",
        name="SignalHire",
        config={},
        credentials={"api_key": TOKEN},
    )


@pytest.fixture
def world(fake: FakeSignalHire) -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    company = make_company(campaign=campaign, name="Atlas Freight")
    return {"client": client, "company": company, "api": api_for("manager", client)}


@pytest.mark.django_db
def test_finding_people_saves_contacts_with_the_provider_as_source(world) -> None:
    connect(world["client"], "people_data")
    response = world["api"].post(f"/api/v1/companies/{world['company'].pk}/find-people/", {})
    assert response.status_code == 200, response.content
    data = response.json()
    assert [c["name"] for c in data["added"]] == ["Sara Haddad"]
    assert data["added"][0]["source"] == "SignalHire people search"
    contact = Contact.objects.get(company=world["company"])
    assert contact.data_source.provider_reference == f"signalhire:{UID}"
    again = world["api"].post(f"/api/v1/companies/{world['company'].pk}/find-people/", {}).json()
    assert (again["added"], again["skipped"]) == ([], 1)


@pytest.mark.django_db
def test_without_a_connection_the_message_says_what_to_add(world) -> None:
    response = world["api"].post(f"/api/v1/companies/{world['company'].pk}/find-people/", {})
    assert response.status_code == 400
    assert "SignalHire" in response.content.decode()


@pytest.mark.django_db
def test_an_email_is_looked_up_only_for_an_approved_account(world) -> None:
    connect(world["client"], "email_enrichment")
    contact = make_contact(company=world["company"], name="Sara Haddad", email="")
    url = f"/api/v1/companies/{world['company'].pk}/find-email/"
    refused = world["api"].post(url, {"contact_id": str(contact.pk)}, format="json")
    assert refused.status_code == 400
    make_human_decision(company=world["company"], decision="add")
    contact.profile_url = LINKEDIN
    contact.save()
    ok = world["api"].post(url, {"contact_id": str(contact.pk)}, format="json")
    assert ok.status_code == 200, ok.content
    assert ok.json()["email"] == "sara@atlas.example.com"
    assert ok.json()["email_status"] == "unverified"


@pytest.mark.django_db
def test_the_connection_test_reads_the_credit_balance(world, fake) -> None:
    services.create_connection(
        world["client"],
        None,
        category="people_data",
        provider="signalhire",
        name="SH",
        config={},
        credentials={"api_key": TOKEN},
    )
    connection = services.resolve_connection(world["client"], "people_data")
    assert connection is not None
    assert check_connection(connection).ok
    assert fake.requests[-1].url.endswith("/credits")
