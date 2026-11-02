"""Find companies with the client's own provider connection (Apollo, People Data Labs).

The import flow used to know only the process-wide providers (the offline test one). A client's
connection to a licensed company-data provider is now listed for its campaigns and searched with
the client's credentials, metered like every provider call. Fake transport only: no network.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.imports import provider_import
from apps.imports.providers import ProviderRegistryError
from apps.integrations import services
from apps.integrations.models import ProviderCall
from tests.factories import make_campaign, make_client
from tests.test_company_api import api_for
from tests.test_company_data_adapters import apollo_routed

pytestmark = [pytest.mark.django_db]

KEY = "YOUR_API_KEY"  # pragma: allowlist secret
NO_FILTERS: dict[str, list[str]] = {"countries": [], "industries": []}


def connect(client: Any, provider: str = "apollo", *, key: bool = True) -> None:
    services.create_connection(
        client,
        None,
        category="company_data",
        provider=provider,
        name=provider.title(),
        config={},
        credentials={"api_key": KEY} if key else None,
    )


@pytest.fixture
def fake_apollo(monkeypatch: pytest.MonkeyPatch) -> Any:
    transport = apollo_routed()
    monkeypatch.setattr(
        "apps.integrations.adapters.apollo.adapter.UrllibTransport",
        lambda hosts, provider_name: transport,
    )
    return transport


def names(response: Any) -> list[str]:
    body = response.json()
    rows = body["results"] if isinstance(body, dict) else body
    return [row["name"] for row in rows]


def test_a_connected_provider_is_listed_for_its_campaign_only(fake_apollo: Any) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    connect(client)
    api = api_for("viewer", client)
    assert "apollo" not in names(api.get("/api/v1/providers/"))
    listed = api.get(f"/api/v1/providers/?campaign={campaign.pk}")
    assert listed.status_code == 200
    assert "apollo" in names(listed)
    row = next(r for r in listed.json()["results"] if r["name"] == "apollo")
    assert row["supports_search"] is True
    assert row["terms"] == "licensed_api"
    assert KEY not in listed.content.decode()


def test_a_provider_that_cannot_search_is_not_listed(fake_apollo: Any) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    connect(client, "hunter")  # Hunter has no company search
    listed = api_for("viewer", client).get(f"/api/v1/providers/?campaign={campaign.pk}")
    assert "hunter" not in names(listed)


def test_another_clients_campaign_is_not_found(fake_apollo: Any) -> None:
    other = make_campaign(client=make_client())
    api = api_for("viewer", make_client())
    assert api.get(f"/api/v1/providers/?campaign={other.pk}").status_code == 404
    assert api.get("/api/v1/providers/?campaign=not-a-uuid").status_code == 404


def test_a_search_uses_the_clients_connection_and_is_metered(fake_apollo: Any) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    connect(client)
    response = api_for("manager", client).post(
        f"/api/v1/campaigns/{campaign.pk}/provider-search/",
        {"provider": "apollo", "query": "Acme", "filters": NO_FILTERS},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert [item["company"]["name"] for item in body["items"]] == ["Acme Logistics"]
    request = fake_apollo.requests[0]
    assert request.headers["x-api-key"] == KEY
    assert "q_organization_name=Acme" in request.url
    assert ProviderCall.objects.filter(client=client, operation="search").count() == 1


def test_a_provider_the_client_has_not_connected_is_unknown(fake_apollo: Any) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    response = api_for("manager", client).post(
        f"/api/v1/campaigns/{campaign.pk}/provider-search/",
        {"provider": "apollo", "filters": NO_FILTERS},
        format="json",
    )
    assert response.status_code == 400
    assert fake_apollo.requests == []


def test_another_clients_connection_is_never_used(fake_apollo: Any) -> None:
    connect(make_client())  # someone else's key
    client = make_client()
    campaign = make_campaign(client=client)
    response = api_for("manager", client).post(
        f"/api/v1/campaigns/{campaign.pk}/provider-search/",
        {"provider": "apollo", "filters": NO_FILTERS},
        format="json",
    )
    assert response.status_code == 400
    assert fake_apollo.requests == []


def test_a_connection_without_a_key_says_what_to_do(fake_apollo: Any) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    connect(client, key=False)
    response = api_for("manager", client).post(
        f"/api/v1/campaigns/{campaign.pk}/provider-search/",
        {"provider": "apollo", "filters": NO_FILTERS},
        format="json",
    )
    assert response.status_code == 400
    assert "no API key" in response.content.decode()
    assert fake_apollo.requests == []


def test_the_service_resolves_the_connection_for_the_campaign(fake_apollo: Any) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    with pytest.raises(ProviderRegistryError):
        provider_import.get_provider("apollo", campaign=campaign)
    connect(client)
    provider = provider_import.get_provider("apollo", campaign=campaign)
    assert provider.name == "apollo"
    assert provider.capabilities.terms == "licensed_api"
    # Without a campaign there is no client to look the connection up for.
    with pytest.raises(ProviderRegistryError):
        provider_import.get_provider("apollo")
