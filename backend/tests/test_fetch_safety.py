"""SSRF guard, URL parsing and the denylist of the website fetcher (issue #94)."""

from __future__ import annotations

import ipaddress

import pytest

from apps.integrations.fetch import (
    FetchBlockedByPolicy,
    FetchBlockedUnsafeTarget,
    FetchInvalidUrl,
    FetchNetworkError,
    ip_is_public,
    is_denied_host,
)
from apps.integrations.fetch.safety import (
    check_policy,
    parse_target,
    resolve_public,
    system_resolver,
)
from tests import fetch_helpers
from tests.fetch_helpers import FakeResolver

no_network = fetch_helpers.no_network
pytestmark = pytest.mark.usefixtures("no_network")

USERINFO = "http://admin" + ":x@example.com/"

BLOCKED_IPS = [
    "127.0.0.1",
    "127.1.2.3",
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",  # cloud metadata
    "169.254.0.1",
    "100.64.0.1",  # CGNAT
    "100.127.255.254",
    "0.0.0.0",  # noqa: S104 (a fixture: the unspecified address must be refused)
    "0.1.2.3",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    "192.0.2.1",
    "198.18.0.1",
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd12:3456::1",
    "ff02::1",
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:a9fe:a9fe",  # the same, hex form
    "64:ff9b::7f00:1",  # NAT64
    "2002:7f00:1::",  # 6to4 of 127.0.0.1
    "2001:db8::1",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    "fec0::1",
]
PUBLIC_IPS = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]


@pytest.mark.parametrize("address", BLOCKED_IPS)
def test_non_public_addresses_are_rejected(address: str) -> None:
    assert ip_is_public(address) is False


@pytest.mark.parametrize("address", PUBLIC_IPS)
def test_public_addresses_are_accepted(address: str) -> None:
    assert ip_is_public(address) is True


def test_garbage_is_not_public() -> None:
    assert ip_is_public("not an ip") is False
    assert ip_is_public("") is False


@pytest.mark.parametrize("address", BLOCKED_IPS)
def test_ip_literal_urls_to_private_ranges_are_blocked(address: str) -> None:
    host = f"[{address}]" if ":" in address else address
    target = parse_target(f"http://{host}/admin")
    with pytest.raises(FetchBlockedUnsafeTarget) as exc:
        resolve_public(target, FakeResolver())
    assert exc.value.reason == "private_address"


@pytest.mark.parametrize("address", PUBLIC_IPS)
def test_public_ip_literal_is_allowed(address: str) -> None:
    host = f"[{address}]" if ":" in address else address
    target = parse_target(f"https://{host}/")
    resolver = FakeResolver()
    (resolved,) = resolve_public(target, resolver)
    assert ipaddress.ip_address(resolved) == ipaddress.ip_address(address)
    assert resolver.calls == []  # an IP literal needs no DNS


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f000001/",  # hex
        "http://0x7f.0.0.1/",
        "http://017700000001/",  # octal
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://127.0.1/",
        "http://1.2.3/",
        "http://4294967295/",
        "http://3232235777/",  # 192.168.1.1
        "http://0/",
        "http://0x0/",
        "http://[::1%25eth0]/",  # IPv6 zone id
        "http://127。0。0。1/",  # ideographic full stops
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_numeric_host_tricks_never_reach_the_network(url: str) -> None:
    try:
        target = parse_target(url)
    except FetchBlockedUnsafeTarget:
        return  # refused at parse time
    with pytest.raises(FetchBlockedUnsafeTarget):
        resolve_public(target, FakeResolver())


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("ftp://example.com/", "scheme_not_allowed"),
        ("gopher://example.com/", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("data:text/html,hi", "scheme_not_allowed"),
        ("FILE:///etc/passwd", "scheme_not_allowed"),
        (USERINFO, "userinfo_not_allowed"),
        ("http://example.com@evil.example/", "userinfo_not_allowed"),
        ("https://example.com:443@127.0.0.1/", "userinfo_not_allowed"),
        ("http://example.com:8080/", "port_not_allowed"),
        ("https://example.com:22/", "port_not_allowed"),
        ("http://example.com:6379/", "port_not_allowed"),
        ("https://example.com:65535/", "port_not_allowed"),
    ],
)
def test_scheme_userinfo_and_port_rules(url: str, reason: str) -> None:
    with pytest.raises(FetchBlockedUnsafeTarget) as exc:
        parse_target(url)
    assert exc.value.reason == reason
    assert "admin" not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "example.com",
        "http://",
        "http:///path",
        "http://exa mple.com/",
        "http://example.com/\nHost: evil",
        "http://example.com\\@evil.com/",
        "http://[::1/",
        "http://example.com:notaport/",
        "http://example.com:99999/",
        "x" * 3000,
    ],
)
def test_invalid_urls(url: str) -> None:
    with pytest.raises((FetchInvalidUrl, FetchBlockedUnsafeTarget)):
        parse_target(url)


@pytest.mark.parametrize("value", [None, 5, b"http://example.com", ["http://example.com"]])
def test_non_string_url(value: object) -> None:
    with pytest.raises(FetchInvalidUrl):
        parse_target(value)


def test_default_ports_and_normalisation() -> None:
    assert parse_target("HTTP://Example.COM/a?x=1#frag").url == "http://example.com/a?x=1"
    with pytest.raises(FetchInvalidUrl):
        parse_target("https://example.com/a b")
    assert parse_target("https://example.com").path == "/"
    assert parse_target("https://example.com:443/x").url == "https://example.com/x"
    assert parse_target("http://example.com./").host == "example.com"
    assert parse_target("https://example.com:8443/", allowed_ports=(8443,)).port == 8443
    assert parse_target("https://[2606:4700::1111]:443/").authority == "[2606:4700::1111]"


def test_unicode_and_punycode_hosts() -> None:
    assert parse_target("http://exämple.com/").host == "xn--exmple-cua.com"
    assert parse_target("http://xn--exmple-cua.com/").host == "xn--exmple-cua.com"
    assert parse_target("https://münchen.example/").host == "xn--mnchen-3ya.example"
    with pytest.raises((FetchInvalidUrl, FetchBlockedUnsafeTarget)):
        parse_target("http://xn--/")
    with pytest.raises((FetchInvalidUrl, FetchBlockedUnsafeTarget)):
        parse_target("http://" + "a" * 70 + ".com/")


def test_resolver_answers_are_all_checked() -> None:
    target = parse_target("https://example.com/")
    resolver = FakeResolver({"example.com": ["93.184.216.34", "10.0.0.5"]})
    with pytest.raises(FetchBlockedUnsafeTarget):
        resolve_public(target, resolver)


@pytest.mark.parametrize(
    "answers",
    [["127.0.0.1"], ["169.254.169.254"], ["::1"], ["::ffff:7f00:1"], ["192.168.0.10"]],
)
def test_hostname_resolving_to_internal_address_is_blocked(answers: list[str]) -> None:
    target = parse_target("https://innocent.example/")
    with pytest.raises(FetchBlockedUnsafeTarget):
        resolve_public(target, FakeResolver({"innocent.example": answers}))


def test_unresolvable_host_is_a_network_error_not_an_ssrf_block() -> None:
    target = parse_target("https://nowhere.example/")
    with pytest.raises(FetchNetworkError):
        resolve_public(target, FakeResolver({"nowhere.example": OSError("no such host")}))
    with pytest.raises(FetchNetworkError):
        resolve_public(target, FakeResolver({"nowhere.example": []}))


def test_scoped_ipv6_answers_are_stripped_and_checked() -> None:
    target = parse_target("https://example.com/")
    with pytest.raises(FetchBlockedUnsafeTarget):
        resolve_public(target, FakeResolver({"example.com": ["fe80::1%eth0"]}))


def test_system_resolver_uses_getaddrinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def fake(
        host: str, port: int, type: int = 0
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (2, 1, 6, "", ("93.184.216.34", port)),
            (2, 1, 6, "", ("93.184.216.34", port)),
            (10, 1, 6, "", ("2606:4700::1111", port, 0, 0)),  # type: ignore[list-item]
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert system_resolver("example.com", 443) == ["93.184.216.34", "2606:4700::1111"]


@pytest.mark.parametrize(
    "host",
    [
        "linkedin.com",
        "www.linkedin.com",
        "uk.linkedin.com",
        "sub.linkedin.com",
        "a.b.linkedin.com",
        "LinkedIn.COM",
        "linkedin.com.",
        "facebook.com",
        "m.facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "mobile.twitter.com",
        "lnkd.in",
    ],
)
def test_denylist_blocks(host: str) -> None:
    assert is_denied_host(host) is True
    with pytest.raises(FetchBlockedByPolicy) as exc:
        check_policy(parse_target(f"https://{host.rstrip('.')}/in/someone"))
    assert "no unauthorized scraping" in str(exc.value)
    assert "Brief" in str(exc.value)
    assert exc.value.code == "fetch_blocked_policy"


@pytest.mark.parametrize(
    "host",
    [
        "evil-linkedin.com",
        "notlinkedin.com",
        "linkedin.com.evil.example",
        "linkedin.example",
        "mylinkedin.com",
        "box.com",  # ends with "x.com" but not on a label boundary
        "fox.com",
        "index.com",
        "example.com",
    ],
)
def test_denylist_does_not_block_lookalikes(host: str) -> None:
    assert is_denied_host(host) is False
    check_policy(parse_target(f"https://{host}/"))


@pytest.mark.parametrize(
    "url",
    ["https://ＬＩＮＫＥＤＩＮ.com/", "https://linkedin。com/", "https://wwｗ.linkedin.com/"],  # noqa: RUF001 (the fullwidth look-alikes are the point of this test)
)
def test_denylist_sees_through_unicode_lookalikes(url: str) -> None:
    with pytest.raises(FetchBlockedByPolicy):
        check_policy(parse_target(url))


def test_denylist_can_be_extended_but_not_shrunk(settings) -> None:
    assert is_denied_host("social.example") is False
    settings.WEBSITE_FETCH = {"extra_denied_hosts": ["Social.Example", "", 5]}
    assert is_denied_host("social.example") is True
    assert is_denied_host("a.social.example") is True
    assert is_denied_host("linkedin.com") is True
    settings.WEBSITE_FETCH = {"extra_denied_hosts": []}
    assert is_denied_host("linkedin.com") is True
