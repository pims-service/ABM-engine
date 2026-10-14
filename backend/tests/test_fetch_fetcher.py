"""PublicWebFetcher behaviour (issue #94): robots.txt, redirects, limits, content rules, opt-outs.

No network, no DNS and no waiting: the transport, the resolver and the clock are all fakes.
"""

from __future__ import annotations

import gzip
import zlib
from typing import Any

import pytest

from apps.integrations.fetch import (
    FetchBlockedByPolicy,
    FetchBlockedByRobots,
    FetchBlockedUnsafeTarget,
    FetchError,
    FetchHttpError,
    FetchInvalidUrl,
    FetchNetworkError,
    FetchOptedOut,
    FetchTimeout,
    FetchTooLarge,
    FetchUnsupportedContent,
)
from apps.integrations.fetch.robots import agent_token
from apps.integrations.fetch.transport import TransportError
from tests import fetch_helpers
from tests.fetch_helpers import (
    NOW,
    PUBLIC_IP,
    FakeResolver,
    FakeResponse,
    make_fetcher,
    page,
    redirect,
    robots,
    timeout_error,
)

no_network = fetch_helpers.no_network
pytestmark = pytest.mark.usefixtures("no_network")

HOME = "https://example.com/"


def html(body: str = "<p>Hello world</p>", head: str = "") -> FakeResponse:
    return page(f"<html><head>{head}</head><body>{body}</body></html>")


# ------------------------------------------------------------------ the happy path


def test_fetch_returns_text_metadata_and_provenance() -> None:
    head = (
        "<title>Acme Ltd</title>"
        '<meta name="description" content="We make anvils.">'
        '<link rel="canonical" href="https://example.com/home">'
    )
    body = "<h1>Acme</h1><p>Hello world</p>"
    fetcher, _, _, _ = make_fetcher(
        {"example.com/": page(f"<html lang='en'><head>{head}</head><body>{body}</body></html>")}
    )
    result = fetcher.fetch(HOME, purpose="company_profile")

    assert result.status == 200
    assert result.final_url == HOME
    assert result.content_type == "text/html"
    assert "Hello world" in result.text
    assert result.title == "Acme Ltd"
    assert result.description == "We make anvils."
    assert result.canonical_url == "https://example.com/home"
    assert result.lang == "en"
    assert result.redirect_chain == ()
    assert result.truncated is False
    assert result.purpose == "company_profile"
    assert result.fetched_at == NOW
    assert result.content_hash.startswith("sha256:")
    assert result.robots.reason == "no_robots_txt"
    assert result.robots.allowed is True
    assert result.bytes_read >= result.decoded_bytes > 0

    record = result.provenance()
    assert record["source_url"] == HOME
    assert record["retrieved_at"] == NOW.isoformat()
    assert record["content_hash"] == result.content_hash
    assert record["robots"] == "no_robots_txt"
    assert record["purpose"] == "company_profile"


def test_same_body_same_hash_and_different_body_different_hash() -> None:
    first, _, _, _ = make_fetcher({"example.com/": html("<p>one</p>")})
    again, _, _, _ = make_fetcher({"example.com/": html("<p>one</p>")})
    other, _, _, _ = make_fetcher({"example.com/": html("<p>two</p>")})
    a = first.fetch(HOME, purpose="t").content_hash
    assert a == again.fetch(HOME, purpose="t").content_hash
    assert a != other.fetch(HOME, purpose="t").content_hash


def test_plain_text_pages_are_returned_as_text() -> None:
    plain = FakeResponse(200, b"We are Acme.\nWe make anvils.", content_type="text/plain")
    fetcher, _, _, _ = make_fetcher({"example.com/": plain})
    result = fetcher.fetch(HOME, purpose="t")
    assert result.content_type == "text/plain"
    assert "We make anvils." in result.text


def test_requests_are_honest_and_carry_no_cookies_or_credentials() -> None:
    fetcher, transport, _, _ = make_fetcher({"example.com/": html()})
    fetcher.fetch(HOME, purpose="t")
    assert transport.requests, "the fetcher should have made requests"
    for request in transport.requests:
        names = {name.lower() for name in request.headers}
        assert "cookie" not in names
        assert "authorization" not in names
        assert request.headers["User-Agent"] == fetcher.user_agent
        assert "gzip" in request.headers["Accept-Encoding"]
        assert request.addresses == (PUBLIC_IP,)


def test_robots_txt_is_requested_before_the_page() -> None:
    fetcher, transport, _, _ = make_fetcher({"example.com/": html()})
    fetcher.fetch(HOME, purpose="t")
    assert transport.urls()[0] == "https://example.com/robots.txt"
    assert transport.urls()[1] == HOME


@pytest.mark.parametrize("purpose", ["", "Has Spaces", "x" * 200, "semi;colon"])
def test_a_malformed_purpose_is_a_caller_bug(purpose: str) -> None:
    fetcher, _, _, _ = make_fetcher({"example.com/": html()})
    with pytest.raises(ValueError, match="purpose"):
        fetcher.fetch(HOME, purpose=purpose)


# ------------------------------------------------------------------ robots.txt


def test_a_disallowed_path_is_not_fetched() -> None:
    routes: dict[str, Any] = {
        "example.com/robots.txt": robots("User-agent: *\nDisallow: /private"),
        "example.com/private/x": html(),
    }
    fetcher, transport, _, _ = make_fetcher(routes)
    with pytest.raises(FetchBlockedByRobots):
        fetcher.fetch("https://example.com/private/x", purpose="t")
    assert transport.urls() == ["https://example.com/robots.txt"]


def test_an_allowed_path_on_a_site_with_rules_is_fetched() -> None:
    routes: dict[str, Any] = {
        "example.com/robots.txt": robots("User-agent: *\nDisallow: /private"),
        "example.com/public": html(),
    }
    fetcher, _, _, _ = make_fetcher(routes)
    result = fetcher.fetch("https://example.com/public", purpose="t")
    assert result.robots.reason == "allowed"


def test_a_group_naming_our_agent_wins_over_the_wildcard() -> None:
    fetcher, _, _, _ = make_fetcher({})
    token = agent_token(fetcher.user_agent)
    text = f"User-agent: {token}\nAllow: /\n\nUser-agent: *\nDisallow: /\n"
    fetcher, _, _, _ = make_fetcher(
        {"example.com/robots.txt": robots(text), "example.com/": html()}
    )
    assert fetcher.fetch(HOME, purpose="t").robots.allowed is True


def test_a_wildcard_disallow_everything_blocks_the_site() -> None:
    routes: dict[str, Any] = {"example.com/robots.txt": robots("User-agent: *\nDisallow: /")}
    fetcher, _, _, _ = make_fetcher(routes)
    with pytest.raises(FetchBlockedByRobots):
        fetcher.fetch(HOME, purpose="t")


@pytest.mark.parametrize("status", [500, 502, 503])
def test_an_unreachable_robots_txt_means_disallowed(status: int) -> None:
    routes: dict[str, Any] = {
        "example.com/robots.txt": robots("oops", status=status),
        "example.com/": html(),
    }
    fetcher, transport, _, _ = make_fetcher(routes)
    with pytest.raises(FetchBlockedByRobots):
        fetcher.fetch(HOME, purpose="t")
    assert HOME not in transport.urls()


def test_a_robots_txt_timeout_means_disallowed() -> None:
    routes: dict[str, Any] = {"example.com/robots.txt": timeout_error(), "example.com/": html()}
    fetcher, _, _, _ = make_fetcher(routes)
    with pytest.raises(FetchBlockedByRobots):
        fetcher.fetch(HOME, purpose="t")


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_a_missing_or_forbidden_robots_txt_means_no_rules(status: int) -> None:
    routes: dict[str, Any] = {
        "example.com/robots.txt": robots("nope", status=status),
        "example.com/": html(),
    }
    fetcher, _, _, _ = make_fetcher(routes)
    result = fetcher.fetch(HOME, purpose="t")
    assert result.robots.allowed is True
    assert result.robots.reason == "no_robots_txt"


def test_robots_txt_is_cached_per_host() -> None:
    routes: dict[str, Any] = {"example.com/a": html(), "example.com/b": html()}
    fetcher, transport, _, _ = make_fetcher(routes)
    fetcher.fetch("https://example.com/a", purpose="t")
    fetcher.fetch("https://example.com/b", purpose="t")
    assert transport.urls().count("https://example.com/robots.txt") == 1


def test_crawl_delay_is_honoured_and_capped() -> None:
    # The delay applies to the request after the one that read it, so fetch two pages.
    for text, expected_min, expected_max in (
        ("Crawl-delay: 5", 4.99, 5.01),
        ("Crawl-delay: 5000", 29.99, 30.01),
    ):
        routes: dict[str, Any] = {
            "example.com/robots.txt": robots(f"User-agent: *\n{text}"),
            "example.com/a": html(),
            "example.com/b": html(),
        }
        fetcher, _, clock, _ = make_fetcher(routes)
        fetcher.fetch("https://example.com/a", purpose="t")
        fetcher.fetch("https://example.com/b", purpose="t")
        assert expected_min <= max(clock.sleeps) <= expected_max


def test_requests_to_one_host_are_spaced_by_the_minimum_delay() -> None:
    routes: dict[str, Any] = {"example.com/a": html(), "example.com/b": html()}
    fetcher, _, clock, _ = make_fetcher(routes)
    fetcher.fetch("https://example.com/a", purpose="t")
    before = len(clock.sleeps)
    fetcher.fetch("https://example.com/b", purpose="t")
    assert len(clock.sleeps) > before
    assert max(clock.sleeps) >= 0.99


# ------------------------------------------------------------------ redirects


def test_redirects_are_followed_and_recorded() -> None:
    routes: dict[str, Any] = {
        "a.example/": redirect("https://b.example/x"),
        "b.example/x": html("<p>moved</p>"),
    }
    fetcher, _, _, _ = make_fetcher(routes)
    result = fetcher.fetch("https://a.example/", purpose="t")
    assert result.final_url == "https://b.example/x"
    assert result.redirect_chain[0].startswith("https://a.example")
    assert "moved" in result.text


def test_relative_redirects_are_resolved() -> None:
    routes: dict[str, Any] = {
        "example.com/old": redirect("/new"),
        "example.com/new": html("<p>new</p>"),
    }
    fetcher, _, _, _ = make_fetcher(routes)
    result = fetcher.fetch("https://example.com/old", purpose="t")
    assert result.final_url == "https://example.com/new"


def test_a_redirect_into_a_private_address_is_blocked_before_connecting() -> None:
    routes: dict[str, Any] = {"a.example/": redirect("https://evil.example/")}
    resolver = FakeResolver({"evil.example": ["10.0.0.5"]})
    fetcher, transport, _, _ = make_fetcher(routes, resolver=resolver)
    with pytest.raises(FetchBlockedUnsafeTarget):
        fetcher.fetch("https://a.example/", purpose="t")
    assert "evil.example" not in transport.hosts_contacted()


@pytest.mark.parametrize(
    "target",
    [
        "ftp://files.example/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pw@example.com/",  # pragma: allowlist secret
        "http://example.com:8080/",
    ],
)
def test_unsafe_redirect_targets_are_refused(target: str) -> None:
    fetcher, transport, _, _ = make_fetcher({"example.com/": redirect(target)})
    with pytest.raises(FetchError) as caught:
        fetcher.fetch(HOME, purpose="t")
    assert caught.value.code in {"fetch_blocked_unsafe", "fetch_invalid_url"}
    assert transport.hosts_contacted() == {"example.com"}


def test_a_redirect_into_the_denylist_is_blocked() -> None:
    fetcher, transport, _, _ = make_fetcher(
        {"example.com/": redirect("https://www.linkedin.com/company/acme")}
    )
    with pytest.raises(FetchBlockedByPolicy):
        fetcher.fetch(HOME, purpose="t")
    assert transport.hosts_contacted() == {"example.com"}


def test_redirect_loops_stop() -> None:
    fetcher, _, _, _ = make_fetcher({"example.com/": redirect("https://example.com/")})
    with pytest.raises(FetchBlockedUnsafeTarget) as caught:
        fetcher.fetch(HOME, purpose="t")
    assert caught.value.reason == "too_many_redirects"


def test_a_redirect_without_a_location_is_an_http_error() -> None:
    fetcher, _, _, _ = make_fetcher({"example.com/": FakeResponse(302, content_type=None)})
    with pytest.raises(FetchHttpError):
        fetcher.fetch(HOME, purpose="t")


# ------------------------------------------------------------------ the safety rules in a fetch


def test_denylisted_hosts_are_never_contacted() -> None:
    fetcher, transport, _, resolver = make_fetcher({})
    for url in ("https://www.linkedin.com/company/acme", "https://x.com/acme"):
        with pytest.raises(FetchBlockedByPolicy):
            fetcher.fetch(url, purpose="t")
    assert transport.requests == []
    assert resolver.calls == []


def test_a_host_resolving_to_a_private_address_is_blocked() -> None:
    resolver = FakeResolver({"example.com": ["192.168.1.10"]})
    fetcher, transport, _, _ = make_fetcher({"example.com/": html()}, resolver=resolver)
    with pytest.raises(FetchBlockedUnsafeTarget):
        fetcher.fetch(HOME, purpose="t")
    assert transport.requests == []


def test_dns_is_checked_again_on_every_fetch() -> None:
    # The first fetch sees a public address, the next one an internal address (DNS rebinding).
    resolver = FakeResolver({"example.com": [[PUBLIC_IP], ["10.0.0.1"]]})
    fetcher, transport, _, _ = make_fetcher({"example.com/": html()}, resolver=resolver)
    fetcher.fetch(HOME, purpose="t")
    seen = len(transport.requests)
    with pytest.raises(FetchBlockedUnsafeTarget):
        fetcher.fetch(HOME, purpose="t")
    assert len(transport.requests) == seen


@pytest.mark.parametrize(
    "url", ["", "not a url", "ftp://example.com/", "https://", "//example.com"]
)
def test_invalid_urls_are_rejected(url: str) -> None:
    fetcher, transport, _, _ = make_fetcher({})
    with pytest.raises((FetchInvalidUrl, FetchBlockedUnsafeTarget)):
        fetcher.fetch(url, purpose="t")
    assert transport.requests == []


# ------------------------------------------------------------------ content and size rules


@pytest.mark.parametrize("mime", ["application/pdf", "image/png", "application/octet-stream"])
def test_unsupported_content_types_are_refused(mime: str) -> None:
    binary = FakeResponse(200, b"%PDF-1.4 ...", content_type=mime)
    fetcher, _, _, _ = make_fetcher({"example.com/": binary})
    with pytest.raises(FetchUnsupportedContent):
        fetcher.fetch(HOME, purpose="t")


def test_a_page_over_the_size_cap_is_refused(settings: Any) -> None:
    settings.WEBSITE_FETCH = {"max_bytes": 200, "max_compressed_bytes": 200}
    big = page("<html><body>" + "<p>filler text</p>" * 200 + "</body></html>")
    fetcher, _, _, _ = make_fetcher({"example.com/": big})
    with pytest.raises(FetchTooLarge):
        fetcher.fetch(HOME, purpose="t")


def test_a_gzip_page_is_decoded() -> None:
    raw = b"<html><body><p>compressed hello</p></body></html>"
    compressed = FakeResponse(200, gzip.compress(raw), headers=[("Content-Encoding", "gzip")])
    fetcher, _, _, _ = make_fetcher({"example.com/": compressed})
    result = fetcher.fetch(HOME, purpose="t")
    assert "compressed hello" in result.text
    assert result.decoded_bytes == len(raw)
    assert result.bytes_read < result.decoded_bytes + 200


def test_a_deflate_page_is_decoded() -> None:
    raw = b"<html><body><p>deflated hello</p></body></html>"
    deflated = FakeResponse(200, zlib.compress(raw), headers=[("Content-Encoding", "deflate")])
    fetcher, _, _, _ = make_fetcher({"example.com/": deflated})
    assert "deflated hello" in fetcher.fetch(HOME, purpose="t").text


def test_a_decompression_bomb_is_stopped() -> None:
    bomb = FakeResponse(
        200,
        gzip.compress(b"a" * (8 * 1024 * 1024)),
        headers=[("Content-Encoding", "gzip")],
    )
    fetcher, _, _, _ = make_fetcher({"example.com/": bomb})
    with pytest.raises(FetchTooLarge):
        fetcher.fetch(HOME, purpose="t")


def test_corrupt_compressed_data_is_refused() -> None:
    corrupt = FakeResponse(200, b"this is not gzip", headers=[("Content-Encoding", "gzip")])
    fetcher, _, _, _ = make_fetcher({"example.com/": corrupt})
    with pytest.raises(FetchUnsupportedContent):
        fetcher.fetch(HOME, purpose="t")


def test_an_unknown_content_encoding_is_refused() -> None:
    odd = FakeResponse(200, b"<html></html>", headers=[("Content-Encoding", "br")])
    fetcher, _, _, _ = make_fetcher({"example.com/": odd})
    with pytest.raises(FetchUnsupportedContent):
        fetcher.fetch(HOME, purpose="t")


def test_long_text_is_cut_and_flagged(settings: Any) -> None:
    settings.WEBSITE_FETCH = {"max_text_chars": 60}
    long_page = html("<p>" + "word " * 200 + "</p>")
    fetcher, _, _, _ = make_fetcher({"example.com/": long_page})
    result = fetcher.fetch(HOME, purpose="t")
    assert result.truncated is True
    assert len(result.text) <= 60


# ------------------------------------------------------------------ errors from the site


def test_error_statuses_are_typed() -> None:
    fetcher, _, _, _ = make_fetcher({})  # every page 404s
    with pytest.raises(FetchHttpError) as missing:
        fetcher.fetch(HOME, purpose="t")
    assert missing.value.status == 404
    assert missing.value.retryable is False

    server, _, _, _ = make_fetcher({"example.com/": FakeResponse(503, content_type="text/plain")})
    with pytest.raises(FetchHttpError) as down:
        server.fetch(HOME, purpose="t")
    assert down.value.status == 503
    assert down.value.retryable is True


def test_a_429_carries_the_retry_after_hint() -> None:
    limited = FakeResponse(429, headers=[("Retry-After", "7")], content_type="text/plain")
    fetcher, _, _, _ = make_fetcher({"example.com/": limited})
    with pytest.raises(FetchHttpError) as caught:
        fetcher.fetch(HOME, purpose="t")
    assert caught.value.status == 429
    assert caught.value.retryable is True
    assert caught.value.retry_after == 7


def test_timeouts_and_network_failures_are_typed() -> None:
    slow, _, _, _ = make_fetcher({"example.com/": timeout_error()})
    with pytest.raises(FetchTimeout):
        slow.fetch(HOME, purpose="t")

    broken, _, _, _ = make_fetcher({"example.com/": TransportError("connection reset")})
    with pytest.raises(FetchNetworkError):
        broken.fetch(HOME, purpose="t")


def test_errors_never_leak_the_url_path_or_query() -> None:
    fetcher, _, _, _ = make_fetcher({})
    with pytest.raises(FetchHttpError) as caught:
        fetcher.fetch("https://example.com/secret/path?token=abc", purpose="t")
    shown = str(caught.value) + str(caught.value.as_dict())
    assert "secret/path" not in shown
    assert "token=abc" not in shown


# ------------------------------------------------------------------ opt-outs


@pytest.mark.parametrize("value", ["noai", "noindex", "none", "NoAI, nofollow", "noindex,noai"])
def test_an_x_robots_tag_opt_out_discards_the_content(value: str) -> None:
    tagged = FakeResponse(
        200, b"<html><body>hello</body></html>", headers=[("X-Robots-Tag", value)]
    )
    fetcher, _, _, _ = make_fetcher({"example.com/": tagged})
    with pytest.raises(FetchOptedOut):
        fetcher.fetch(HOME, purpose="t")


@pytest.mark.parametrize("content", ["noai", "noindex", "none", "noindex, nofollow"])
def test_a_meta_robots_opt_out_discards_the_content(content: str) -> None:
    head = f'<meta name="robots" content="{content}">'
    fetcher, _, _, _ = make_fetcher({"example.com/": html(head=head)})
    with pytest.raises(FetchOptedOut):
        fetcher.fetch(HOME, purpose="t")


def test_harmless_robots_directives_are_not_an_opt_out() -> None:
    head = '<meta name="robots" content="index, follow">'
    tagged = FakeResponse(
        200,
        f"<html><head>{head}</head><body>fine</body></html>".encode(),
        headers=[("X-Robots-Tag", "nosnippet")],
    )
    fetcher, _, _, _ = make_fetcher({"example.com/": tagged})
    assert "fine" in fetcher.fetch(HOME, purpose="t").text


def test_content_language_is_used_when_the_page_has_no_lang() -> None:
    localised = FakeResponse(
        200, b"<html><body>marhaba</body></html>", headers=[("Content-Language", "ar-SA")]
    )
    fetcher, _, _, _ = make_fetcher({"example.com/": localised})
    assert fetcher.fetch(HOME, purpose="t").lang.lower().startswith("ar")
