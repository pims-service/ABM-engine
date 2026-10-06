"""Domain normalization table (issue #40)."""

from __future__ import annotations

import pytest

from apps.companies.domain import normalize_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Example.com/path?q=1#frag", "example.com"),
        ("http://example.com", "example.com"),
        ("example.com", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("www.example.com", "example.com"),
        ("//www.example.com/x", "example.com"),
        ("https://example.com:8443/a", "example.com"),
        ("https://user:pw@example.com/", "example.com"),  # pragma: allowlist secret
        ("https://example.com./", "example.com"),
        ("  https://example.com/  ", "example.com"),
        ("example.com/some/page", "example.com"),
        ("ftp://files.example.co.uk", "files.example.co.uk"),
        ("https://eu.example.com", "eu.example.com"),  # subdomains are kept (open question 11)
        ("https://www.www.example.com", "www.example.com"),  # only one leading www is dropped
        ("https://bücher.de/katalog", "xn--bcher-kva.de"),
        ("https://شركة.السعودية", "xn--ogbpi5d.xn--mgberp4a5d4ar"),
        ("https://my-shop_1.example.org", "my-shop_1.example.org"),
    ],
)
def test_normalizes(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "localhost",
        "http://localhost:8000",
        "https://192.168.0.1/admin",
        "https://",
        "not a url",
        "https://exa mple.com",
        "https://-bad.example.com",
        "https://" + "a" * 64 + ".com",
        "https://" + ".".join(["abcdefghij"] * 26) + ".com",
        "https://[::1]/",
        "https://[bad",
    ],
)
def test_unusable_input_gives_no_domain(raw):
    assert normalize_domain(raw) is None


def test_idempotent():
    once = normalize_domain("HTTPS://WWW.Bücher.DE/x")
    assert once is not None
    assert normalize_domain(once) == once
