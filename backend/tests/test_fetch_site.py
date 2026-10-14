"""fetch_site_pages: homepage plus a few high-value pages (issue #94). No network."""

from __future__ import annotations

from typing import Any

import pytest

from apps.integrations.fetch import FetchBlockedByRobots, fetch_site_pages
from tests import fetch_helpers
from tests.fetch_helpers import FakeResponse, make_fetcher, page, robots

no_network = fetch_helpers.no_network
pytestmark = pytest.mark.usefixtures("no_network")


def doc(title: str, links: str = "") -> FakeResponse:
    body = f"<p>{title} text</p>{links}"
    return page(f"<html><head><title>{title}</title></head><body>{body}</body></html>")


HOME_LINKS = (
    '<a href="/about">About us</a>'
    '<a href="/products">Products</a>'
    '<a href="/careers">Careers</a>'
    '<a href="/news">News</a>'
    '<a href="/contact">Contact</a>'
    '<a href="/blog/random-post">A post</a>'
)


def site_routes() -> dict[str, Any]:
    return {
        "example.com/": doc("Home", HOME_LINKS),
        "example.com/about": doc("About"),
        "example.com/products": doc("Products"),
        "example.com/careers": doc("Careers"),
        "example.com/news": doc("News"),
        "example.com/contact": doc("Contact"),
    }


def test_home_and_one_page_per_kind_in_priority_order() -> None:
    fetcher, _, _, _ = make_fetcher(site_routes())
    result = fetch_site_pages("https://example.com/", max_pages=5, fetcher=fetcher)
    assert [p.kind for p in result.pages] == ["home", "about", "products", "careers", "news"]
    assert result.skipped == ()
    assert result.budget_exhausted is False
    assert result.bytes_read > 0


def test_each_page_carries_its_provenance() -> None:
    fetcher, _, _, _ = make_fetcher(site_routes())
    result = fetch_site_pages("https://example.com/", max_pages=3, fetcher=fetcher)
    for site_page in result.pages:
        assert site_page.provenance["source_url"] == site_page.result.final_url
        assert site_page.provenance["content_hash"].startswith("sha256:")
        assert site_page.provenance["purpose"] == "company_site"


def test_max_pages_is_respected_and_clamped() -> None:
    fetcher, _, _, _ = make_fetcher(site_routes())
    assert len(fetch_site_pages("https://example.com/", max_pages=1, fetcher=fetcher).pages) == 1
    fetcher, _, _, _ = make_fetcher(site_routes())
    assert len(fetch_site_pages("https://example.com/", max_pages=0, fetcher=fetcher).pages) == 1


def test_a_failing_page_is_skipped_and_the_run_goes_on() -> None:
    routes = site_routes()
    del routes["example.com/about"]  # 404
    fetcher, _, _, _ = make_fetcher(routes)
    result = fetch_site_pages("https://example.com/", max_pages=5, fetcher=fetcher)
    kinds = [p.kind for p in result.pages]
    assert "about" not in kinds
    assert "home" in kinds
    assert [(s.kind, s.code) for s in result.skipped] == [("about", "fetch_http_error")]


def test_a_page_blocked_by_robots_is_skipped_not_fetched() -> None:
    routes = site_routes()
    routes["example.com/robots.txt"] = robots("User-agent: *\nDisallow: /careers")
    fetcher, transport, _, _ = make_fetcher(routes)
    result = fetch_site_pages("https://example.com/", max_pages=5, fetcher=fetcher)
    assert any(s.kind == "careers" and s.code == "fetch_blocked_robots" for s in result.skipped)
    assert "https://example.com/careers" not in transport.urls()


def test_a_failing_homepage_raises() -> None:
    routes = {"example.com/robots.txt": robots("User-agent: *\nDisallow: /")}
    fetcher, _, _, _ = make_fetcher(routes)
    with pytest.raises(FetchBlockedByRobots):
        fetch_site_pages("https://example.com/", fetcher=fetcher)


def test_links_to_other_sites_and_social_networks_are_never_followed() -> None:
    links = (
        '<a href="https://www.linkedin.com/company/acme/about">About</a>'
        '<a href="https://other.org/about">About</a>'
        '<a href="/about">About</a>'
    )
    fetcher, transport, _, _ = make_fetcher(
        {"example.com/": doc("Home", links), "example.com/about": doc("About")}
    )
    result = fetch_site_pages("https://example.com/", fetcher=fetcher)
    assert [p.kind for p in result.pages] == ["home", "about"]
    assert transport.hosts_contacted() == {"example.com"}


def test_the_byte_budget_stops_further_fetches(settings: Any) -> None:
    settings.WEBSITE_FETCH = {"site_total_bytes": 1}
    fetcher, _, _, _ = make_fetcher(site_routes())
    result = fetch_site_pages("https://example.com/", max_pages=4, fetcher=fetcher)
    assert [p.kind for p in result.pages] == ["home"]
    assert result.budget_exhausted is True
    assert {s.code for s in result.skipped} == {"budget"}


def test_a_homepage_without_useful_links_gives_just_the_homepage() -> None:
    fetcher, _, _, _ = make_fetcher({"example.com/": doc("Home", '<a href="/x?y=1">x</a>')})
    result = fetch_site_pages("https://example.com/", fetcher=fetcher)
    assert [p.kind for p in result.pages] == ["home"]
    assert result.skipped == ()
