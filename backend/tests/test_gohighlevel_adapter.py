"""The GoHighLevel adapter against a fake transport (no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.gohighlevel import API_VERSION, GoHighLevelAdapter
from apps.integrations.framework import (
    CRMContact,
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUpstreamError,
)

pytestmark = pytest.mark.unit

TOKEN = "YOUR_API_KEY"  # pragma: allowlist secret
CONFIG = {"location_id": "LOC123abc"}


class FakeHighLevel:
    """Answers like HighLevel; ``new`` says whether the upsert creates the contact."""

    def __init__(self, *, new: bool = True, status: int = 200, contact_id: str | None = "cid1"):
        self.new = new
        self.status = status
        self.contact_id = contact_id
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.status != 200:
            return HttpResponse(self.status, {"retry-after": "7"}, b"{}")
        if request.url.endswith("/contacts/upsert"):
            contact = {"id": self.contact_id} if self.contact_id else {}
            body: dict[str, Any] = {"new": self.new, "contact": contact, "traceId": "t-1"}
        elif request.url.endswith("/tags"):
            body = {"tags": ["ABM Engine"]}
        elif request.url.endswith("/notes"):
            body = {"note": {"id": "n1"}}
        else:
            body = {"location": {"id": "LOC123abc"}}
        return HttpResponse(200, {}, json.dumps(body).encode())

    def paths(self) -> list[str]:
        return [r.url.split("leadconnectorhq.com")[1] for r in self.requests]

    def body(self, suffix: str) -> dict[str, Any]:
        request = next(r for r in self.requests if r.url.endswith(suffix))
        return json.loads(request.body or b"{}")  # type: ignore[no-any-return]


def adapter(fake: FakeHighLevel, **config: Any) -> GoHighLevelAdapter:
    return GoHighLevelAdapter({**CONFIG, **config}, {"api_key": TOKEN}, transport=fake)


def contact(**fields: str) -> CRMContact:
    return CRMContact(
        "contact:1",
        "Sara Haddad",
        email="sara@example.com",
        title="CEO",
        fields={
            "company_name": "Atlas Freight",
            "note_1": "Summary line",
            "note_2": "Message",
            **fields,
        },
    )


def test_a_new_contact_is_upserted_tagged_and_gets_the_note() -> None:
    fake = FakeHighLevel(new=True)
    result = adapter(fake).push_contact(contact())
    assert result.data is not None
    assert (result.data.action, result.data.remote_id) == ("created", "cid1")
    assert fake.paths() == ["/contacts/upsert", "/contacts/cid1/tags", "/contacts/cid1/notes"]
    upsert = fake.body("/contacts/upsert")
    assert upsert["locationId"] == "LOC123abc"
    assert upsert["email"] == "sara@example.com"
    assert upsert["companyName"] == "Atlas Freight"
    assert upsert["source"] == "ABM Engine"
    assert "tags" not in upsert  # an upsert would REPLACE the contact's tags
    assert fake.body("/tags") == {"tags": ["ABM Engine"]}
    note = fake.body("/notes")["body"]
    assert note.splitlines() == ["Title: CEO", "Summary line", "Message"]


def test_requests_carry_the_token_and_the_api_version() -> None:
    fake = FakeHighLevel()
    adapter(fake).push_contact(contact())
    for request in fake.requests:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Version"] == API_VERSION
        assert request.url.startswith("https://services.leadconnectorhq.com/")
        assert TOKEN not in repr(request)


def test_an_existing_contact_is_updated_and_the_note_is_not_added_twice() -> None:
    fake = FakeHighLevel(new=False)
    result = adapter(fake).push_contact(contact())
    assert result.data is not None
    assert result.data.action == "updated"
    assert fake.paths() == ["/contacts/upsert", "/contacts/cid1/tags"]


def test_the_note_can_be_added_again_on_request() -> None:
    fake = FakeHighLevel(new=False)
    adapter(fake).push_contact(contact(refresh_note="1"))
    assert fake.paths()[-1] == "/contacts/cid1/notes"


def test_the_tag_and_source_are_configurable() -> None:
    fake = FakeHighLevel()
    adapter(fake, tag="Outbound KSA", source="Growviah").push_contact(contact())
    assert fake.body("/tags") == {"tags": ["Outbound KSA"]}
    assert fake.body("/contacts/upsert")["source"] == "Growviah"


def test_provider_errors_are_typed_and_never_contain_the_token() -> None:
    with pytest.raises(ProviderAuthError) as auth:
        adapter(FakeHighLevel(status=401)).push_contact(contact())
    assert TOKEN not in str(auth.value)
    with pytest.raises(ProviderRateLimited) as limited:
        adapter(FakeHighLevel(status=429)).push_contact(contact())
    assert limited.value.retry_after == 7


def test_an_answer_without_a_contact_id_is_an_upstream_error() -> None:
    with pytest.raises(ProviderUpstreamError):
        adapter(FakeHighLevel(contact_id=None)).push_contact(contact())


def test_config_needs_a_location_and_a_highlevel_host() -> None:
    assert GoHighLevelAdapter.validate_config(CONFIG, {"api_key": TOKEN}).ok
    assert not GoHighLevelAdapter.validate_config({}, {"api_key": TOKEN}).ok
    assert not GoHighLevelAdapter.validate_config(
        {**CONFIG, "api_base": "https://evil.example"}, {"api_key": TOKEN}
    ).ok
    assert not GoHighLevelAdapter.validate_config({"location_id": "bad id!"}, {"api_key": TOKEN}).ok
    assert not GoHighLevelAdapter.validate_config(CONFIG, {}).ok


def test_the_connection_test_reads_the_location() -> None:
    fake = FakeHighLevel()
    assert adapter(fake).test_connection().ok
    assert fake.paths() == ["/locations/LOC123abc"]
    failed = adapter(FakeHighLevel(status=401)).test_connection()
    assert not failed.ok
    assert failed.error_code
    assert TOKEN not in failed.message
