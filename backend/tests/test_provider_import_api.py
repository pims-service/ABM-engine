"""Provider import API (issue #67): providers list, preview, import, status, errors, roles."""

from __future__ import annotations

import json
from typing import Any

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.imports.models import ImportBatch
from apps.imports.providers import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
    default_registry,
)
from tests.factories import make_campaign, make_client, make_company, make_user

pytestmark = pytest.mark.django_db


def api_for(user: User | None) -> APIClient:
    api = APIClient()
    if user is not None:
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def member(client: Any, role: str) -> User:
    user = make_user()
    grant_membership(client, user, role)
    return user


@pytest.fixture
def campaign() -> Campaign:
    return make_campaign(profile__countries=["SA"], profile__industries=["Fintech"])


@pytest.fixture
def manager(campaign: Campaign) -> APIClient:
    return api_for(member(campaign.client, "manager"))


NO_FILTERS: dict[str, Any] = {
    "industries": [],
    "employee_min": None,
    "employee_max": None,
    "business_model": "",
}


def search_url(c: Campaign) -> str:
    return f"/api/v1/campaigns/{c.pk}/provider-search/"


def import_url(c: Campaign) -> str:
    return f"/api/v1/campaigns/{c.pk}/provider-import/"


def search(api: APIClient, c: Campaign, **body: Any) -> Any:
    return api.post(search_url(c), {"provider": "fake", **body}, format="json")


# ------------------------------------------------------------------ providers


def test_provider_list_has_capabilities_and_no_secrets(manager: APIClient) -> None:
    data = manager.get("/api/v1/providers/").json()
    assert data["count"] == 1
    item = data["results"][0]
    assert item["name"] == "fake"
    assert item["terms"] == "manual"
    assert item["supports_filters"]["countries"] is True
    assert item["max_import_selection"] == 100
    assert item["inline_import_max"] == 25
    assert set(item) == {
        "name",
        "terms",
        "supports_search",
        "supports_filters",
        "max_page_size",
        "requires_credentials",
        "max_import_selection",
        "inline_import_max",
    }


def test_provider_list_empty_when_none_registered(manager: APIClient) -> None:
    default_registry.clear()
    assert manager.get("/api/v1/providers/").json()["results"] == []


def test_provider_list_needs_login() -> None:
    assert api_for(None).get("/api/v1/providers/").status_code == 401


# ------------------------------------------------------------------ preview


def test_preview_uses_campaign_defaults_and_writes_nothing(
    manager: APIClient, campaign: Campaign
) -> None:
    before = (Company.objects.count(), ImportBatch.objects.count())
    response = search(manager, campaign)
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake"
    assert body["search"]["countries"] == ["SA"]
    assert body["search"]["industries"] == ["Fintech"]
    assert [i["company"]["name"] for i in body["items"]] == ["Desert Pay"]
    item = body["items"][0]
    assert item["dedupe"]["status"] == "new"
    assert item["importable"] is True
    assert item["provider_key"] == "fake:fake-desert-pay"
    assert body["counts"]["new"] == 1
    assert (Company.objects.count(), ImportBatch.objects.count()) == before


def test_preview_overrides_query_paging_and_dedupe_flags(
    manager: APIClient, campaign: Campaign
) -> None:
    make_company(campaign=campaign, name="Mine", website="https://www.najd-tech.example.com")
    first = search(manager, campaign, filters={**NO_FILTERS, "countries": ["SA"]}, page_size=2)
    body = first.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"]
    statuses = {i["company"]["name"]: i["dedupe"] for i in body["items"]}
    assert any(
        d["status"] == "duplicate" and d["company"]["name"] == "Mine" for d in statuses.values()
    )
    second = search(
        manager,
        campaign,
        filters={**NO_FILTERS, "countries": ["SA"]},
        page_size=2,
        cursor=body["next_cursor"],
    ).json()
    assert (
        second["items"][0]["company"]["provider_id"] != body["items"][0]["company"]["provider_id"]
    )
    assert search(manager, campaign, query="pay", filters={"industries": []}).json()["items"]


def test_preview_validation_errors(manager: APIClient, campaign: Campaign) -> None:
    r = search(manager, campaign, filters={"countries": ["ZZ"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"
    assert "countries" in r.json()["error"]["details"]
    r = manager.post(search_url(campaign), {"provider": "nope"}, format="json")
    assert r.status_code == 400
    assert "provider" in r.json()["error"]["details"]
    assert manager.post(search_url(campaign), {}, format="json").status_code == 400
    assert search(manager, campaign, page_size=1000).status_code == 400


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ProviderRateLimited(42), 429, "provider_rate_limited"),
        (ProviderUnavailable(), 503, "provider_unavailable"),
        (ProviderAuthError(), 502, "provider_auth"),
        (ProviderQuotaExceeded(), 502, "provider_quota_exceeded"),
        (ProviderBadRequest("query"), 400, "provider_bad_request"),
    ],
)
def test_provider_errors_are_mapped_to_safe_envelopes(
    manager: APIClient, campaign: Campaign, error: Any, status: int, code: str
) -> None:
    default_registry.get("fake").fail_with = error  # type: ignore[attr-defined]
    response = search(manager, campaign)
    assert response.status_code == status
    err = response.json()["error"]
    assert err["code"] == code
    assert err["message"] == str(error)
    assert err["details"]["provider"] == "fake"
    if isinstance(error, ProviderRateLimited):
        assert err["details"]["retry_after"] == 42
        assert response["Retry-After"] == "42"


def test_upstream_text_does_not_leak(manager: APIClient, campaign: Campaign) -> None:
    secret = "sk-live-abc123"  # pragma: allowlist secret

    class Boom(Exception):
        pass

    def explode(request: Any) -> Any:
        raise Boom(f"Authorization: Bearer {secret} body={secret}")

    default_registry.get("fake").search = explode  # type: ignore[method-assign]
    response = search(manager, campaign)
    assert response.status_code == 503
    assert secret not in response.content.decode()


# ------------------------------------------------------------------ import


def test_import_inline_returns_201_with_counts(manager: APIClient, campaign: Campaign) -> None:
    items = search(manager, campaign, filters={"industries": []}).json()["items"]
    chosen = [i["company"] for i in items[:2]]
    response = manager.post(
        import_url(campaign), {"provider": "fake", "companies": chosen}, format="json"
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "completed"
    assert body["created_count"] == 2
    assert body["background"] is False
    assert [r["outcome"] for r in body["rows"]] == ["created", "created"]
    assert Company.objects.filter(campaign=campaign, input_source="provider").count() == 2
    status = manager.get(f"/api/v1/provider-imports/{body['id']}/")
    assert status.status_code == 200
    assert status.json()["created_count"] == 2
    # Second time: duplicates, no new companies.
    again = manager.post(
        import_url(campaign), {"provider": "fake", "companies": chosen}, format="json"
    ).json()
    assert again["duplicate_count"] == 2
    assert Company.objects.filter(campaign=campaign).count() == 2


def test_import_by_ids_with_search(manager: APIClient, campaign: Campaign) -> None:
    response = manager.post(
        import_url(campaign),
        {
            "provider": "fake",
            "provider_ids": ["fake-desert-pay"],
            "search": {"filters": {"industries": []}},
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["created_count"] == 1


def test_large_import_is_accepted_and_runs_as_a_job(manager: APIClient, campaign: Campaign) -> None:
    companies = [
        {"name": f"Bulk {n}", "website": f"https://bulk{n}.example.com", "provider_id": f"b{n}"}
        for n in range(26)
    ]
    response = manager.post(
        import_url(campaign), {"provider": "fake", "companies": companies}, format="json"
    )
    assert response.status_code == 202, response.content
    body = response.json()
    assert body["background"] is True
    assert body["job_id"]
    polled = manager.get(f"/api/v1/provider-imports/{body['id']}/").json()
    assert polled["status"] == "completed"
    assert polled["created_count"] == 26


def test_selection_cap_and_shape(manager: APIClient, campaign: Campaign, settings: Any) -> None:
    settings.PROVIDER_IMPORT_MAX_SELECTION = 2
    many = [{"name": f"C{n}", "provider_id": f"x{n}"} for n in range(3)]
    r = manager.post(import_url(campaign), {"provider": "fake", "companies": many}, format="json")
    assert r.status_code == 400
    assert "selection" in r.json()["error"]["details"]
    assert ImportBatch.objects.count() == 0
    for body in ({"provider": "fake"}, {"provider": "fake", "provider_ids": []}):
        assert manager.post(import_url(campaign), body, format="json").status_code == 400
    both = {"provider": "fake", "provider_ids": ["a"], "companies": [{"name": "A"}]}
    assert manager.post(import_url(campaign), both, format="json").status_code == 400
    future = {
        "provider": "fake",
        "companies": [{"name": "A"}],
        "retrieved_at": "2999-01-01T00:00:00Z",
    }
    assert manager.post(import_url(campaign), future, format="json").status_code == 400


def test_import_provider_failure_is_a_failed_batch_not_an_error(
    manager: APIClient, campaign: Campaign
) -> None:
    default_registry.get("fake").fail_with = ProviderRateLimited(5)  # type: ignore[attr-defined]
    response = manager.post(
        import_url(campaign),
        {"provider": "fake", "provider_ids": ["fake-desert-pay"]},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["provider_error"]["code"] == "provider_rate_limited"
    assert body["rows"][0]["error_code"] == "provider_rate_limited"
    assert "Try again later" in body["error_summary"]


def test_weak_duplicate_is_flagged_in_rows(manager: APIClient, campaign: Campaign) -> None:
    make_company(campaign=campaign, name="Weak Co", website="", country="SA")
    body = manager.post(
        import_url(campaign),
        {
            "provider": "fake",
            "companies": [
                {"name": "Weak Co", "website": "https://weak-other.example.com", "country": "SA"}
            ],
        },
        format="json",
    ).json()
    assert body["rows"][0]["possible_duplicate"] is True
    assert body["rows"][0]["candidate_id"]


# ------------------------------------------------------------------ permissions


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("viewer", False), ("reviewer", False), ("manager", True), ("admin", True)],
)
def test_role_matrix(campaign: Campaign, role: str, allowed: bool) -> None:
    api = api_for(member(campaign.client, role))
    s = search(api, campaign)
    i = api.post(
        import_url(campaign), {"provider": "fake", "companies": [{"name": "A"}]}, format="json"
    )
    assert (s.status_code == 200) is allowed
    assert (i.status_code == 201) is allowed
    if not allowed:
        assert s.status_code == i.status_code == 403


def test_status_is_readable_by_a_viewer(campaign: Campaign, manager: APIClient) -> None:
    batch_id = manager.post(
        import_url(campaign), {"provider": "fake", "companies": [{"name": "A"}]}, format="json"
    ).json()["id"]
    viewer = api_for(member(campaign.client, "viewer"))
    assert viewer.get(f"/api/v1/provider-imports/{batch_id}/").status_code == 200


def test_cross_client_is_404(campaign: Campaign, manager: APIClient) -> None:
    other_user = api_for(member(make_client(), "admin"))
    assert search(other_user, campaign).status_code == 404
    assert (
        other_user.post(import_url(campaign), {"provider": "fake"}, format="json").status_code
        == 404
    )
    batch_id = manager.post(
        import_url(campaign), {"provider": "fake", "companies": [{"name": "A"}]}, format="json"
    ).json()["id"]
    assert other_user.get(f"/api/v1/provider-imports/{batch_id}/").status_code == 404


def test_anonymous_gets_401(campaign: Campaign) -> None:
    assert search(api_for(None), campaign).status_code == 401


def test_archived_campaign_import_is_400(campaign: Campaign, manager: APIClient) -> None:
    from apps.campaigns import services

    services.archive_campaign(campaign)
    r = manager.post(
        import_url(campaign), {"provider": "fake", "companies": [{"name": "A"}]}, format="json"
    )
    assert r.status_code == 400


def test_throttle_is_per_user(campaign: Campaign, manager: APIClient, monkeypatch: Any) -> None:
    from rest_framework.throttling import ScopedRateThrottle

    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {"provider_search": "2/hour", "provider_import": "2/hour"},
    )
    assert [search(manager, campaign).status_code for _ in range(3)] == [200, 200, 429]
    last = search(manager, campaign).json()["error"]
    assert last["code"] == "throttled"
    assert "retry_after" in last["details"]
    other = api_for(member(campaign.client, "manager"))
    assert search(other, campaign).status_code == 200


def test_no_secrets_in_response_bodies(manager: APIClient, campaign: Campaign) -> None:
    text = json.dumps(search(manager, campaign).json()) + json.dumps(
        manager.get("/api/v1/providers/").json()
    )
    for word in ("password", "api_key", "authorization", "secret"):
        assert word not in text.lower()
