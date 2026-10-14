"""POST /api/v1/companies/{id}/website-research/: read a company's public site (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.integrations.fetch import fetch_site_pages
from tests.factories import make_campaign, make_client, make_company
from tests.fetch_helpers import make_fetcher, page, robots
from tests.test_company_api import api_for

pytestmark = [pytest.mark.api, pytest.mark.django_db]

HOME = (
    "<html><head><title>Acme Logistics</title>"
    '<meta name="description" content="Freight for Europe."></head>'
    '<body><h1>Acme</h1><p>We move freight.</p><a href="/careers">Careers</a></body></html>'
)
CAREERS = "<html><head><title>Jobs</title></head><body><p>We are hiring drivers.</p></body></html>"


def use_site(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> None:
    fetcher, _, _, _ = make_fetcher(routes)

    def fake(url: str, **kwargs: Any) -> Any:
        return fetch_site_pages(url, fetcher=fetcher, **kwargs)

    monkeypatch.setattr("apps.companies.api.website_research.fetch_site_pages", fake)


@pytest.fixture
def setup() -> tuple[APIClient, Any]:
    client = make_client()
    company = make_company(campaign=make_campaign(client=client), website="https://example.com")
    return api_for("manager", client), company


def run(api: APIClient, company: Any) -> Any:
    return api.post(f"/api/v1/companies/{company.pk}/website-research/")


def test_returns_pages_with_provenance(monkeypatch, setup) -> None:
    use_site(monkeypatch, {"example.com/": page(HOME), "example.com/careers": page(CAREERS)})
    api, company = setup
    response = run(api, company)
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["error"] is None
    assert [p["kind"] for p in data["pages"]] == ["home", "careers"]
    home = data["pages"][0]
    assert home["title"] == "Acme Logistics"
    assert home["description"] == "Freight for Europe."
    assert "We move freight." in home["excerpt"]
    assert home["content_hash"].startswith("sha256:")
    assert home["fetched_at"]
    assert data["bytes_read"] > 0


def test_a_site_that_blocks_us_is_reported_not_an_error(monkeypatch, setup) -> None:
    use_site(monkeypatch, {"example.com/robots.txt": robots("User-agent: *\nDisallow: /")})
    api, company = setup
    response = run(api, company)
    assert response.status_code == 200
    data = response.json()
    assert data["pages"] == []
    assert data["error"]["code"] == "fetch_blocked_robots"


def test_a_company_without_a_website(setup) -> None:
    api, company = setup
    company.website = ""
    company.save()
    data = run(api, company).json()
    assert data["error"]["code"] == "no_website"
    assert data["pages"] == []


def test_a_bare_domain_is_fetched_over_https(monkeypatch, setup) -> None:
    use_site(monkeypatch, {"example.com/": page(HOME)})
    api, company = setup
    company.website = "example.com"
    company.save()
    assert run(api, company).json()["pages"][0]["kind"] == "home"


def test_a_social_profile_is_never_fetched(monkeypatch, setup) -> None:
    use_site(monkeypatch, {})
    api, company = setup
    company.website = "https://www.linkedin.com/company/acme"
    company.save()
    data = run(api, company).json()
    assert data["error"]["code"] == "fetch_blocked_policy"
