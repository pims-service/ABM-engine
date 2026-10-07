"""Website, profile-URL and name normalization tables (issue #57). Pure: no DB, no network."""
# ruff: noqa: RUF001, S311

from __future__ import annotations

import random
import socket

import pytest

from apps.companies.domain import normalize_domain
from apps.companies.normalize import (
    MAX_NAME_LENGTH,
    MAX_URL_LENGTH,
    ClassifiedInput,
    MatchKey,
    classify_input,
    company_match_keys,
    name_key,
    normalize_company_input,
    normalize_company_name,
    normalize_profile_url,
    normalize_website,
)
from apps.companies.public_suffix import (
    MULTI_PART_SUFFIXES,
    SUFFIX_LIST_VERSION,
    registrable_domain,
)

# ------------------------------------------------------------------------------ websites


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Acme.com/about", "https://acme.com/about"),
        ("acme.com", "https://acme.com"),
        ("http://acme.com/", "https://acme.com"),
        ("HTTP://ACME.COM", "https://acme.com"),
        ("  https://acme.com/  ", "https://acme.com"),
        ("//www.acme.com/x/", "https://acme.com/x"),  # protocol-relative
        ("acme.com/a/b/", "https://acme.com/a/b"),
        ("https://acme.com:443/a", "https://acme.com/a"),
        ("http://acme.com:80/a", "https://acme.com/a"),
        ("acme.com:80", "https://acme.com"),
        ("https://acme.com:8443/a", "https://acme.com:8443/a"),
        ("example.com:8080/x", "https://example.com:8080/x"),
        ("https://acme.com./", "https://acme.com"),
        ("https://acme.com/a#section", "https://acme.com/a"),
        ("https://acme.com/#!/route", "https://acme.com"),
        ("https://acme.com/?utm_source=x&utm_medium=y", "https://acme.com"),
        ("https://acme.com/p?gclid=1&id=7", "https://acme.com/p?id=7"),
        ("https://acme.com/p?fbclid=abc&ref=home&b=2&a=1", "https://acme.com/p?a=1&b=2"),
        ("https://acme.com/?UTM_Campaign=x&HSA_net=y", "https://acme.com"),
        ("https://acme.com/?q=a+b", "https://acme.com?q=a+b"),
        ("https://acme.com/?flag", "https://acme.com?flag="),
        ("https://bücher.de/katalog", "https://xn--bcher-kva.de/katalog"),
        ("https://شركة.السعودية", "https://xn--ogbpi5d.xn--mgberp4a5d4ar"),
        ("https://acme.com/Ü ", "https://acme.com/%C3%9C"),
        ("https://acme.com/a%20b", "https://acme.com/a%20b"),
        ("https:\\\\acme.com\\a", "https://acme.com/a"),
        ("https://eu.acme.com.sa/ar", "https://eu.acme.com.sa/ar"),
        ("https://www.www.acme.com", "https://www.acme.com"),
        ("ＨＴＴＰＳ://ACME.com", None),  # full-width scheme is not a scheme we trust
    ],
)
def test_normalize_website_table(raw, expected):
    result = normalize_website(raw)
    if expected is None:
        assert result is None
    else:
        assert result is not None
        assert result.url == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        42,
        b"https://acme.com",
        "",
        "   ",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "java\nscript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "file://acme.com/x",
        "mailto:sales@acme.com",
        "tel:+966500000000",
        "vbscript:msgbox",
        "ftp://acme.com",
        "ws://acme.com",
        "blob:https://acme.com/uuid",
        "https://user:pw@acme.com",  # pragma: allowlist secret
        "https://user@acme.com",
        "user:pw@acme.com",  # pragma: allowlist secret
        "https://localhost:8000",
        "http://192.168.0.1/admin",
        "https://[::1]/",
        "https://[2001:db8::1]:8080/",
        "https://[bad",
        "https://acme.com:99999",
        "https://acme.com:abc",
        "https://",
        "not a url",
        "https://exa mple.com",
        "https://acme.com/\x00",
        "https://acme.com/a​b",  # zero-width space
        "https://acme.com/a‮b",  # right-to-left override
        "https://-bad.acme.com",
        "https://" + "a" * 64 + ".com",
        "https://acme.com/" + "a" * 3000,
        "https://acme.com/" + "é" * 1000,  # percent-encoding pushes it past the column size
    ],
)
def test_normalize_website_rejects(raw):
    assert normalize_website(raw) is None


def test_website_result_fields_and_warnings():
    site = normalize_website("http://www.Shop.Acme.com.sa:80/p?utm_source=x&a=1#f")
    assert site is not None
    assert site.url == "https://shop.acme.com.sa/p?a=1"
    assert site.domain == "shop.acme.com.sa" == normalize_domain("http://www.Shop.Acme.com.sa")
    assert site.registrable_domain == "acme.com.sa"
    assert set(site.warnings) == {
        "upgraded_to_https",
        "tracking_params_removed",
        "fragment_removed",
    }
    assert normalize_website("acme.com").warnings == ("scheme_added",)  # type: ignore[union-attr]


def test_website_too_many_query_fields_drops_query():
    raw = "https://acme.com/?" + "&".join(f"k{i}=1" for i in range(200))
    site = normalize_website(raw)
    assert site is not None
    assert site.url == "https://acme.com"
    assert "query_dropped" in site.warnings


# ------------------------------------------------------------------- public suffix handling


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("acme.com", "acme.com"),
        ("www2.acme.com", "acme.com"),
        ("eu.shop.acme.com", "acme.com"),
        ("acme.co.uk", "acme.co.uk"),
        ("a.b.acme.co.uk", "acme.co.uk"),
        ("acme.com.sa", "acme.com.sa"),
        ("portal.acme.com.sa", "acme.com.sa"),
        ("acme.com.au", "acme.com.au"),
        ("hr.acme.co.in", "acme.co.in"),
        ("acme.co.ae", "acme.co.ae"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("co.uk", None),
        ("com.sa", None),
        ("com", None),
        ("", None),
        (None, None),
        ("a..b", None),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_suffix_list_is_small_documented_and_well_formed():
    assert SUFFIX_LIST_VERSION
    assert 0 < len(MULTI_PART_SUFFIXES) < 150
    for suffix in MULTI_PART_SUFFIXES:
        assert suffix == suffix.lower()
        assert suffix.count(".") == 1
        assert all(suffix.split("."))
    assert {"co.uk", "com.sa", "com.au", "co.in"} <= MULTI_PART_SUFFIXES


# ------------------------------------------------------------------------------ profile URLs


@pytest.mark.parametrize(
    ("raw", "slug", "url"),
    [
        ("https://www.linkedin.com/company/acme", "acme", "https://www.linkedin.com/company/acme"),
        ("linkedin.com/company/acme/", "acme", "https://www.linkedin.com/company/acme"),
        (
            "HTTP://LinkedIn.com/Company/Acme-Co",
            "acme-co",
            "https://www.linkedin.com/company/acme-co",
        ),
        (
            "https://sa.linkedin.com/company/acme/about/",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://ae.linkedin.com/company/acme?trk=public",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        ("https://m.linkedin.com/company/acme", "acme", "https://www.linkedin.com/company/acme"),
        (
            "https://mobile.linkedin.com/company/acme",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://www.linkedin.com/company/acme/posts/?feedView=all",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://www.linkedin.com/company/acme/life",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://www.linkedin.com/company/acme/?locale=ar_AE#x",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://www.linkedin.com/en/company/acme",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://www.linkedin.com/ar-sa/company/acme",
            "acme",
            "https://www.linkedin.com/company/acme",
        ),
        (
            "https://www.linkedin.com/company/1234567",
            "1234567",
            "https://www.linkedin.com/company/1234567",
        ),
        (
            "linkedin.com/company/1234567/about",
            "1234567",
            "https://www.linkedin.com/company/1234567",
        ),
        (
            "https://www.linkedin.com/company-beta/1234567/",
            "1234567",
            "https://www.linkedin.com/company/1234567",
        ),
        (
            "https://www.linkedin.com/showcase/acme-labs",
            "acme-labs",
            "https://www.linkedin.com/showcase/acme-labs",
        ),
        (
            "https://www.linkedin.com/school/acme-uni",
            "acme-uni",
            "https://www.linkedin.com/school/acme-uni",
        ),
        (
            "https://www.linkedin.com/company/%D8%B4%D8%B1%D9%83%D8%A9",
            "شركة",
            "https://www.linkedin.com/company/%D8%B4%D8%B1%D9%83%D8%A9",
        ),
        (
            "https://www.linkedin.com/company/شركة/about",
            "شركة",
            "https://www.linkedin.com/company/%D8%B4%D8%B1%D9%83%D8%A9",
        ),
        (
            "https://www.crunchbase.com/organization/acme-inc?x=1",
            "acme-inc",
            "https://www.crunchbase.com/organization/acme-inc",
        ),
    ],
)
def test_normalize_profile_url_known_providers(raw, slug, url):
    result = normalize_profile_url(raw)
    assert result is not None
    assert result.slug == slug
    assert result.url == url
    assert result.provider in {"linkedin", "crunchbase"}


def test_profile_url_kinds_and_identity():
    numeric = normalize_profile_url("linkedin.com/company/12345")
    assert numeric is not None
    assert numeric.is_numeric_id
    assert numeric.provider == "linkedin"
    assert numeric.kind == "company"
    assert numeric.identity == "linkedin:12345"
    named = normalize_profile_url("linkedin.com/company/acme")
    assert named is not None
    assert not named.is_numeric_id
    assert normalize_profile_url("linkedin.com/showcase/x").kind == "showcase"  # type: ignore[union-attr]
    assert normalize_profile_url("crunchbase.com/organization/x").kind == "organization"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("raw", "url"),
    [
        ("https://www.Acme.com/about?utm_source=x#top", "https://acme.com/about"),
        ("acme.com", "https://acme.com"),
        ("https://facebook.com/acme/", "https://facebook.com/acme"),
        ("https://notlinkedin.com/company/acme", "https://notlinkedin.com/company/acme"),
        (
            "https://linkedin.com.evil.example/company/acme",
            "https://linkedin.com.evil.example/company/acme",
        ),
    ],
)
def test_profile_url_generic_fallback(raw, url):
    result = normalize_profile_url(raw)
    assert result is not None
    assert result.provider == "generic"
    assert result.slug is None
    assert result.url == url
    assert not result.is_numeric_id
    assert result.identity == "generic:" + url.removeprefix("https://")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "javascript:alert(1)",
        "https://www.linkedin.com/in/some-person",  # personal profile, not a company
        "https://www.linkedin.com/company/",
        "https://www.linkedin.com/company",
        "https://www.linkedin.com/jobs/view/123",
        "https://www.linkedin.com/",
        "https://www.linkedin.com/company/a b",
        "https://www.linkedin.com/company/" + "a" * 300,
        "https://www.linkedin.com/company/ac$me",
        "https://u:p@linkedin.com/company/acme",  # pragma: allowlist secret
        "https://www.crunchbase.com/person/bob",
        "ftp://linkedin.com/company/acme",
    ],
)
def test_profile_url_rejects(raw):
    assert normalize_profile_url(raw) is None


# ------------------------------------------------------------------------------------ names


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Acme", "acme"),
        ("ACME  Trading,   LLC", "acme trading"),
        ("Acme Trading L.L.C.", "acme trading"),
        ("Acme Trading Co., Ltd.", "acme trading"),
        ("Acme Inc", "acme"),
        ("Acme, Inc.", "acme"),
        ("Acme Pvt. Ltd.", "acme"),
        ("Acme Limited", "acme"),
        ("Acme Corporation", "acme"),
        ("Acme PJSC", "acme"),
        ("Acme FZE", "acme"),
        ("Acme FZ-LLC", "acme"),
        ("Acme GmbH", "acme"),
        ("The Acme Company", "acme"),
        ("Acme & Co.", "acme"),
        ("Smith & Sons Ltd", "smith and sons"),
        ("Smith and Sons", "smith and sons"),
        ("Société Générale S.A.", "societe generale"),
        ("Müller GmbH", "muller"),
        ("ＡＣＭＥ　Ｔｒａｄｉｎｇ", "acme trading"),  # full-width forms (NFKC)
        ("Acme Trading​", "acme trading"),
        ("Acme\tTrading\nLLC", "acme trading"),
        ("Acme-Trading_LLC", "acme trading"),
        ("O'Brien Ltd", "obrien"),
        ("Acme 2000 Ltd", "acme 2000"),
        ("Acme ٢٠٠٠ Ltd", "acme 2000"),  # Arabic-Indic digits
        ("LLC", "llc"),  # never strip the last word
        ("Co. Ltd", "co"),
        # Arabic
        ("شركة الأفق للتجارة ذ.م.م", "الافق للتجاره"),
        ("شركة الافق للتجارة ذ م م", "الافق للتجاره"),
        ("الأفق للتجارة ش.م.ع", "الافق للتجاره"),
        ("الافق للتجارة (ذ.م.م)", "الافق للتجاره"),
        ("الأُفُق للتجارة", "الافق للتجاره"),  # diacritics
        ("الأفـــق للتجارة", "الافق للتجاره"),  # tatweel
        ("إبراهيم وأولاده", "ابراهيم واولاده"),
        ("مؤسسة النور المحدودة", "النور"),
        ("شركة", "شركه"),
        # mixed scripts
        ("Acme الأفق LLC", "acme الافق"),
        ("الأفق Acme", "الافق acme"),
    ],
)
def test_company_name_key(raw, key):
    result = normalize_company_name(raw)
    assert result is not None
    assert result.key == key


def test_arabic_and_latin_variants_share_keys():
    a = normalize_company_name("شركة الأفق للتجارة ذ.م.م")
    b = normalize_company_name("الافق  للتجارة")
    assert a is not None
    assert b is not None
    assert a.key == b.key
    assert a.display != b.display


def test_name_display_is_cleaned_not_folded():
    result = normalize_company_name("  Acme​  Trading\x00,\tLLC ‮")
    assert result is not None
    assert result.display == "Acme Trading, LLC"
    assert result.key == "acme trading"
    assert result.warnings == ()


def test_name_length_is_capped():
    result = normalize_company_name("A" * 10_000)
    assert result is not None
    assert len(result.display) <= MAX_NAME_LENGTH
    assert len(result.key) <= MAX_NAME_LENGTH
    assert result.warnings == ("name_truncated",)


@pytest.mark.parametrize("raw", [None, 5, b"x", "", "   ", "!!!", "...", "​​", "،؛؟"])
def test_company_name_unusable(raw):
    assert normalize_company_name(raw) is None


# ------------------------------------------------------------------------------- match keys


def test_match_keys_order_domain_profile_name():
    keys = company_match_keys(
        "Acme Trading LLC",
        "https://www.acme.com.sa/en",
        "https://sa.linkedin.com/company/Acme/about",
    )
    assert keys == (
        MatchKey("domain", "acme.com.sa"),
        MatchKey("profile", "linkedin:acme"),
        MatchKey("name", "acme trading"),
    )
    assert [str(k) for k in keys] == [
        "domain:acme.com.sa",
        "profile:linkedin:acme",
        "name:acme trading",
    ]


def test_match_keys_same_company_different_spellings_agree():
    a = company_match_keys("ACME Ltd", "http://acme.com/", None)
    b = company_match_keys("Acme Limited.", "https://www.Acme.com/about?utm_source=x", "")
    assert a == b


def test_match_keys_skip_missing_and_bad_parts():
    assert company_match_keys() == ()
    assert company_match_keys("", "", "") == ()
    assert company_match_keys(None, "javascript:alert(1)", "ftp://x") == ()
    assert company_match_keys("Acme") == (MatchKey("name", "acme"),)
    assert company_match_keys(website="acme.com") == (MatchKey("domain", "acme.com"),)
    assert company_match_keys(profile_url="linkedin.com/company/acme") == (
        MatchKey("profile", "linkedin:acme"),
    )


def test_match_keys_generic_profile_url_and_dedupe():
    keys = company_match_keys("Acme", "acme.com", "https://facebook.com/acme")
    assert keys == (
        MatchKey("domain", "acme.com"),
        MatchKey("profile", "generic:facebook.com/acme"),
        MatchKey("name", "acme"),
    )
    assert len(set(keys)) == len(keys)


def test_normalize_company_input_collects_warnings():
    row = normalize_company_input(" Acme LLC ", "javascript:x", "https://www.linkedin.com/in/bob")
    assert row.domain is None
    assert row.profile_url_canonical is None
    assert row.name_normalized == "acme"
    assert row.warnings == ("website_unparseable", "profile_url_unparseable")
    assert row.match_keys == (MatchKey("name", "acme"),)

    ok = normalize_company_input("Acme", "http://acme.com/?utm_x=1", "linkedin.com/company/acme")
    assert ok.domain == "acme.com"
    assert ok.profile_url_canonical == "https://www.linkedin.com/company/acme"
    assert "website:upgraded_to_https" in ok.warnings
    assert "website:tracking_params_removed" in ok.warnings

    blank = normalize_company_input("", None, "  ")
    assert blank.warnings == ()
    assert blank.match_keys == ()


# ---------------------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("", "empty", None),
        ("   ", "empty", None),
        (None, "empty", None),
        ("acme.com", "domain", "acme.com"),
        ("Acme.COM", "domain", "acme.com"),
        ("acme.com.sa", "domain", "acme.com.sa"),
        ("shop.acme.co.uk", "domain", "shop.acme.co.uk"),
        ("شركة.السعودية", "domain", "xn--ogbpi5d.xn--mgberp4a5d4ar"),
        ("www.acme.com", "url", "https://acme.com"),
        ("https://acme.com", "url", "https://acme.com"),
        ("http://acme.com/about?utm_source=x", "url", "https://acme.com/about"),
        ("acme.com/about", "url", "https://acme.com/about"),
        ("acme.com:8080", "url", "https://acme.com:8080"),
        ("//acme.com", "url", "https://acme.com"),
        ("linkedin.com/company/acme/about", "profile_url", "https://www.linkedin.com/company/acme"),
        (
            "https://sa.linkedin.com/company/12345",
            "profile_url",
            "https://www.linkedin.com/company/12345",
        ),
        ("Acme", "name", "Acme"),
        ("Acme Trading LLC", "name", "Acme Trading LLC"),
        ("Acme Inc.", "name", "Acme Inc."),
        ("St.Louis Ltd", "name", "St.Louis Ltd"),
        ("Acme.Net Solutions", "name", "Acme.Net Solutions"),
        ("شركة الأفق", "name", "شركة الأفق"),
        ("localhost", "name", "localhost"),
        ("a.123", "name", "a.123"),
        ("Acme​  Co", "name", "Acme Co"),
        ("javascript:alert(1)", "invalid", None),
        ("mailto:sales@acme.com", "invalid", None),
        ("data:text/html,x", "invalid", None),
        ("file:///etc/passwd", "invalid", None),
        ("ftp://acme.com", "invalid", None),
        ("https://user:pw@acme.com", "invalid", None),  # pragma: allowlist secret
        ("http://192.168.0.1", "invalid", None),
        ("https://exa mple.com", "invalid", None),
        ("!!!", "invalid", None),
    ],
)
def test_classify_input(raw, kind, value):
    assert classify_input(raw) == ClassifiedInput(kind, value)


# ------------------------------------------------------- properties: idempotent, never raise

_HOSTILE = [
    "",
    " ",
    "\x00",
    "\r\n",
    "https://",
    "//",
    "///",
    ":",
    "::",
    "@",
    "https://@",
    "https://a@b@c",
    "[",
    "]",
    "https://[",
    "https://[::1",
    "https://[::1]:99999/",
    "https://[::ffff:1.2.3.4]/",
    "http://0x7f.1",
    "http://2130706433/",
    "http://１２７.0.0.1",
    "https://xn--",
    "https://xn--a.com",
    "https://xn--zzzzzzzzzzzzzzzz.com",
    "https://a.com:",
    "https://a.com:0",
    "https://a.com:-1",
    "https://a.com/%",
    "https://a.com/%zz",
    "https://a.com/?%",
    "https://a.com/?=&=&&",
    "https://a.com/?a=%ff",
    "https://a.com/\ud800",
    "\ud800",
    "ᴀ.com",
    "a。com",
    "A．COM",
    "ａ.ｃｏｍ",
    "https://a.com。",
    "https://a..com",
    ".com",
    "a.",
    ".",
    "..",
    "." * 3000,
    "a" * 5000,
    "é" * 5000,
    "https://" + "a." * 200 + "com",
    "mixed Ωmega.com",
    "аcme.com",  # Cyrillic a
    "acmé.com",
    "linkedin.com/company/" + "%" * 50,
    "linkedin.com/company/%00",
    "linkedin.com/company/%E0%A4%A",
    "linkedin.com/company/‮acme",
    "linkedin.com//company//acme//",
    "linkedin.com/company/acme/../beta",
    "LINKEDIN.COM/COMPANY/ACME",
    "https://linkedin.com@evil.com/company/acme",
    "ＬＩＮＫＥＤＩＮ.com/company/acme",
    "ا" * 400,
    "ـ" * 50,
    "ـ" + "ً" * 100,
    "İstanbul Ltd",
    "ǅ ǆ ß ﬃ ﬁ",
    "\U0001f600 Acme \U0001f600",
]


def _corpus(n: int = 3000) -> list[str]:
    rng = random.Random(57)
    alphabet = [
        *list("abcXYZ019-_.:/?#&=%@[]\\ \t"),
        "http://",
        "https://",
        "www.",
        ".com",
        ".co.uk",
        ".com.sa",
        "linkedin.com/company/",
        "utm_source=",
        "llc",
        "ltd",
        "&",
        "شركة",
        "ذ.م.م",
        "ة",
        "أ",
        "ـ",
        "é",
        "ß",
        "\u200b",
        "٣",
        "\x00",
        "😀",
        "xn--",
        "。",
        "．",
    ]
    return [
        "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) for _ in range(n)
    ] + _HOSTILE


def test_functions_never_raise_on_arbitrary_strings():
    for raw in _corpus():
        normalize_website(raw)
        normalize_profile_url(raw)
        normalize_company_name(raw)
        classify_input(raw)
        company_match_keys(raw, raw, raw)
        normalize_domain(raw)
    for junk in (None, 1, 1.5, b"bytes", [], {}, object()):
        assert normalize_website(junk) is None
        assert normalize_profile_url(junk) is None
        assert normalize_company_name(junk) is None
        assert classify_input(junk).kind == "empty" or classify_input(junk).kind == "invalid"
        assert company_match_keys(junk, junk, junk) == ()


def test_website_is_idempotent():
    seen = 0
    for raw in _corpus():
        first = normalize_website(raw)
        if first is None:
            continue
        seen += 1
        assert len(first.url) <= MAX_URL_LENGTH
        assert first.url.startswith("https://")
        second = normalize_website(first.url)
        assert second is not None, (raw, first.url)
        assert second.url == first.url, raw
        assert second.domain == first.domain
        assert first.domain == normalize_domain(first.url)
    assert seen > 20


def test_profile_url_is_idempotent():
    seen = 0
    for raw in _corpus():
        first = normalize_profile_url(raw)
        if first is None:
            continue
        seen += 1
        second = normalize_profile_url(first.url)
        assert second is not None, (raw, first.url)
        assert second.url == first.url, raw
        assert second.slug == first.slug
        assert second.provider == first.provider
    assert seen > 20


def test_name_normalization_is_idempotent():
    seen = 0
    for raw in [*_corpus(), "Acme Co. Ltd.", "The The", "and", "Acme & Co. & Ltd", "شركة شركة"]:
        first = normalize_company_name(raw)
        if first is None:
            continue
        seen += 1
        assert len(first.display) <= MAX_NAME_LENGTH
        again = normalize_company_name(first.display)
        assert again is not None, raw
        assert again.display == first.display, raw
        assert again.key == first.key, raw
        assert name_key(first.key) == first.key, (raw, first.key)
    assert seen > 100


def test_classify_value_is_a_fixed_point():
    for raw in _corpus(1000):
        first = classify_input(raw)
        if first.value is None or first.kind == "name":
            continue  # a "name" may legitimately contain URL-looking text
        again = classify_input(first.value)
        assert again.kind in {first.kind, "url"}, (raw, first, again)
        if again.kind == first.kind:
            assert again.value == first.value, raw


def test_no_network_is_touched(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network access in a pure utility")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    assert normalize_website("https://acme.com") is not None
    assert normalize_profile_url("linkedin.com/company/acme") is not None
    assert company_match_keys("Acme", "acme.com", "linkedin.com/company/acme")
    assert classify_input("acme.com").kind == "domain"


def test_existing_normalize_domain_behaviour_is_unchanged():
    assert normalize_domain("https://www.Example.com/path?q=1#frag") == "example.com"
    assert normalize_domain("eu.shop.example.co.uk") == "eu.shop.example.co.uk"  # subdomains kept
    assert normalize_domain("co.uk") == "co.uk"  # a bare public suffix is not rejected here
