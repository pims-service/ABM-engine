"""Pure normalization utilities for company input (issue #57).

No database, no network, no third-party dependency. Every public function accepts any ``str``
(and tolerates ``None`` or other junk) and never raises: unusable input gives ``None`` (or an
``invalid`` classification) so callers can record a per-row warning instead of crashing a
whole import.

* ``normalize_website`` -> canonical https URL (``NormalizedWebsite``)
* ``normalize_profile_url`` -> provider + slug + canonical URL, parsing only, we never fetch
  or scrape (``NormalizedProfileUrl``)
* ``normalize_company_name`` -> display name + comparison key (``NormalizedName``)
* ``company_match_keys`` -> ordered identity keys for duplicate detection (domain, profile
  slug, name)
* ``normalize_company_input`` -> all of the above for one row, with warnings
* ``classify_input`` -> is a single free-text box a URL, a domain, a profile URL or a name?

``apps.companies.domain.normalize_domain`` stays the single source of truth for the stored
``Company.domain``; everything here builds on it.
"""
# ruff: noqa: RUF001, RUF002, RUF003

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, NamedTuple
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from .domain import normalize_domain
from .public_suffix import registrable_domain

MAX_INPUT_LENGTH = 2048
"""Raw URLs/domains longer than this are rejected (a real one never is)."""
MAX_URL_LENGTH = 2000
"""Canonical URLs must fit ``Company.website`` / ``profile_url`` (2000)."""
MAX_NAME_LENGTH = 300
"""Matches ``Company.name``; longer names are truncated with a warning."""
MAX_SLUG_LENGTH = 200
_MAX_QUERY_FIELDS = 100

# Whitespace, ASCII/C1 controls, line separators and invisible format characters. Browsers
# silently drop some of these inside URLs ("java\nscript:"), so we reject them rather than guess.
_FORBIDDEN_IN_URL = re.compile(r"[\x00-\x20\x7f-\x9f  -‏ - ⁠﻿]")
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):(//)?", re.IGNORECASE)
_PORT_AFTER_HOST = re.compile(r"^\d{1,5}(?:[/?#]|$)")

_TRACKING_PARAMS = frozenset(
    {
        "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "fbclid", "msclkid", "twclid",
        "ttclid", "yclid", "li_fat_id", "igshid", "mc_cid", "mc_eid", "mkt_tok", "ref",
        "ref_src", "ref_url", "referrer", "_ga", "_gl", "_hsenc", "_hsmi", "trk", "trkid",
        "trkcampaign", "spm", "s_cid", "cmpid", "ocid", "vero_id",
    }
)  # fmt: skip
_TRACKING_PREFIXES = ("utm_", "hsa_", "mtm_", "pk_", "_hs")


# ---------------------------------------------------------------------------------- websites


@dataclass(frozen=True, slots=True)
class NormalizedWebsite:
    """A website in canonical form.

    ``url`` is ``https://<host>[:port][/path][?query]``; ``domain`` equals what
    ``normalize_domain`` stores in ``Company.domain``; ``registrable_domain`` is the
    public-suffix-aware parent (``eu.shop.example.co.uk`` -> ``example.co.uk``).
    """

    url: str
    domain: str
    registrable_domain: str | None
    warnings: tuple[str, ...] = ()


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def _clean_query(query: str, warnings: list[str]) -> str:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=_MAX_QUERY_FIELDS)
    except ValueError:
        warnings.append("query_dropped")
        return ""
    kept = [(k, v) for k, v in pairs if not _is_tracking_param(k)]
    if len(kept) != len(pairs):
        warnings.append("tracking_params_removed")
    return urlencode(sorted(kept))


def normalize_website(raw: object) -> NormalizedWebsite | None:
    """Return the canonical https form of ``raw``, or ``None`` when it is not a usable website.

    Rules: scheme is added when missing and ``http`` is upgraded to ``https``; the host is
    lowercased, punycoded and loses one leading ``www.``; ports 80 and 443 are dropped (other
    ports are kept); fragments and tracking parameters (``utm_*``, ``gclid``, ``fbclid``,
    ``ref`` and friends) are removed and the remaining query is sorted; a trailing slash is
    dropped (``https://example.com``, ``https://example.com/about``). ``None`` is returned for
    non-http(s) schemes (``javascript:``, ``data:``, ``file:``, ``mailto:``, ``ftp:``), embedded
    credentials, IP hosts, single labels, whitespace or control characters inside the value,
    and anything longer than ``MAX_INPUT_LENGTH``. Paths are otherwise kept as given.
    """
    try:
        return _normalize_website(raw)
    except (ValueError, UnicodeError):  # defensive: the contract is "never raise"
        return None


def _normalize_website(raw: object) -> NormalizedWebsite | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("\\", "/")
    if not text or len(text) > MAX_INPUT_LENGTH or _FORBIDDEN_IN_URL.search(text):
        return None
    warnings: list[str] = []
    scheme_match = _SCHEME.match(text)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme_match.group(2):  # "scheme://"
            if scheme not in ("http", "https"):
                return None
            if scheme == "http":
                warnings.append("upgraded_to_https")
            text = text[scheme_match.end() - 2 :]  # keep the leading "//"
        elif not _PORT_AFTER_HOST.match(text[scheme_match.end() :]):
            return None  # javascript:, data:, mailto:, file:, tel: ...
        else:
            text = "//" + text
            warnings.append("scheme_added")
    else:
        text = "//" + text.removeprefix("//") if text.startswith("//") else "//" + text
        warnings.append("scheme_added")
    parts = urlsplit(text)
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        return None
    port = parts.port
    domain = normalize_domain(parts.hostname)
    if domain is None:
        return None
    path = quote(parts.path, safe="/%:@!$&'()*+,;=-._~").rstrip("/")
    query = _clean_query(parts.query, warnings) if parts.query else ""
    if parts.fragment:
        warnings.append("fragment_removed")
    url = "https://" + domain
    if port is not None and port not in (80, 443):
        url += f":{port}"
    url += path
    if query:
        url += "?" + query
    if len(url) > MAX_URL_LENGTH:
        return None
    return NormalizedWebsite(url, domain, registrable_domain(domain), tuple(warnings))


# ------------------------------------------------------------------------------ profile URLs


@dataclass(frozen=True, slots=True)
class NormalizedProfileUrl:
    """A company profile URL reduced to an identifier.

    ``provider`` is ``linkedin``, ``crunchbase`` or ``generic``; ``kind`` is the profile type
    (``company``, ``showcase``, ``school``, ``organization``) or ``generic``; ``slug`` is the
    lower-case identifier (``None`` for generic URLs). ``url`` is the canonical form.
    """

    provider: str
    slug: str | None
    url: str
    kind: str = "company"
    warnings: tuple[str, ...] = ()

    @property
    def is_numeric_id(self) -> bool:
        """True for ``linkedin.com/company/<numeric id>`` style identifiers."""
        return self.slug is not None and self.slug.isdigit()

    @property
    def identity(self) -> str:
        """Stable comparison value: ``linkedin:acme``; generic URLs use host and path."""
        if self.slug is not None:
            return f"{self.provider}:{self.slug}"
        return "generic:" + self.url.removeprefix("https://")


_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$", re.IGNORECASE)
_SLUG_OK = re.compile(r"^[\w.\-]+$")

# provider -> (registrable domain, canonical host, {path segment: kind})
_PROVIDERS: dict[str, tuple[str, str, dict[str, str]]] = {
    "linkedin": (
        "linkedin.com",
        "www.linkedin.com",
        {
            "company": "company",
            "company-beta": "company",
            "showcase": "showcase",
            "school": "school",
        },
    ),
    "crunchbase": ("crunchbase.com", "www.crunchbase.com", {"organization": "organization"}),
}


def _clean_slug(segment: str) -> str | None:
    slug = unicodedata.normalize("NFKC", unquote(segment, errors="replace")).strip().lower()
    if not slug or len(slug) > MAX_SLUG_LENGTH or not _SLUG_OK.match(slug):
        return None
    return slug


def _provider_for(domain: str) -> str | None:
    for provider, (base, _canonical_host, _kinds) in _PROVIDERS.items():
        # linkedin has country and mobile subdomains (sa., uk., m., mobile.); the stored host
        # may already be stripped of "www."
        if domain == base or (provider == "linkedin" and domain.endswith("." + base)):
            return provider
    return None


def normalize_profile_url(raw: object) -> NormalizedProfileUrl | None:
    """Parse a company profile URL into provider and slug; never fetches anything.

    LinkedIn: ``linkedin.com/company/<slug>`` (also ``company-beta``, ``showcase``, ``school``,
    ``www.``/``m.``/``mobile.`` and country subdomains, locale prefixes, query strings and
    trailing ``/about``, ``/posts`` ...) becomes ``https://www.linkedin.com/company/<slug>``
    with a lower-case slug (numeric ids work too). Personal profiles (``/in/...``) and other
    LinkedIn paths give ``None``. Crunchbase ``/organization/<slug>`` is handled the same way.
    Any other http(s) URL falls back to its canonical website form (``provider="generic"``,
    no slug). Non-http(s) schemes and junk give ``None``.
    """
    try:
        return _normalize_profile_url(raw)
    except (ValueError, UnicodeError):  # defensive: the contract is "never raise"
        return None


def _normalize_profile_url(raw: object) -> NormalizedProfileUrl | None:
    site = normalize_website(raw)
    if site is None:
        return None
    provider = _provider_for(site.domain)
    if provider is None:
        return NormalizedProfileUrl("generic", None, site.url, "generic", site.warnings)
    _base, canonical_host, kinds = _PROVIDERS[provider]
    segments = [s for s in urlsplit(site.url).path.split("/") if s]
    if len(segments) > 1 and _LOCALE_SEGMENT.match(segments[0]) and segments[0] not in kinds:
        segments = segments[1:]  # /en/company/acme
    if len(segments) < 2 or segments[0].lower() not in kinds:
        return None
    kind = kinds[segments[0].lower()]
    slug = _clean_slug(segments[1])
    if slug is None:
        return None
    url = f"https://{canonical_host}/{kind}/{quote(slug, safe='-_.~')}"
    return NormalizedProfileUrl(provider, slug, url, kind, site.warnings)


# --------------------------------------------------------------------------------- names

_ARABIC_MARKS = re.compile(r"[̀-ͯؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_LETTER_MAP = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ک": "ك", "ی": "ي"}
)
_NAME_DROPPED = re.compile(r"[.'’ʼ`]")
_NAME_REPLACE_WITH_SPACE = re.compile(r"[^\w]|_", re.UNICODE)

# Legal-form words stripped from the END of a comparison key (after punctuation removal, so
# "L.L.C." arrives as "llc" and "ذ.م.م" as "ذمم"). Arabic entries are already in key form
# (alef variants folded, ة -> ه).
LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        # English and international
        "llc", "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co", "company",
        "plc", "pjsc", "psc", "jsc", "pjs", "fze", "fz", "fzc", "fzco", "fzllc", "llp", "lp",
        "gmbh", "ag", "sa", "sarl", "bv", "nv", "pte", "pvt", "private", "wll", "spc",
        "dmcc", "ksc", "kscp", "bsc", "qsc", "qpsc", "saog", "saoc", "pty", "oy", "ab",
        "as", "srl", "spa", "sas", "kg", "ug", "ltda", "sdn", "bhd",
        # Arabic: ذ.م.م, ش.م.ع, ش.م.خ, ش.ذ.م.م and spelled-out forms
        "ذمم", "شذمم", "شمع", "شمخ", "شمم", "ذ", "م", "ش", "شركه", "موسسه", "محدوده",
        "المحدوده", "مسوليه", "ذات", "مساهمه", "عامه", "خاصه",
    }
)  # fmt: skip
LEADING_WORDS: frozenset[str] = frozenset({"the", "شركه", "موسسه"})
_TRAILING_CONNECTORS = frozenset({"and"})


@dataclass(frozen=True, slots=True)
class NormalizedName:
    """``display`` is for showing (cleaned, original casing); ``key`` is for comparing."""

    display: str
    key: str
    warnings: tuple[str, ...] = ()


def _clean_display(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if ch.isspace() else ch for ch in text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(text.split())


def name_key(display: str) -> str:
    """Comparison key for an already display-cleaned name (see ``normalize_company_name``)."""
    text = _ARABIC_MARKS.sub("", unicodedata.normalize("NFKD", display).casefold())
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ARABIC_LETTER_MAP)
    text = "".join(
        str(unicodedata.decimal(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in text
    )
    text = text.replace("&", " and ")
    text = _NAME_DROPPED.sub("", text)
    text = _NAME_REPLACE_WITH_SPACE.sub(" ", text)
    tokens = text.split()
    while len(tokens) > 1 and tokens[0] in LEADING_WORDS:
        tokens.pop(0)
    while len(tokens) > 1 and (tokens[-1] in LEGAL_SUFFIXES or tokens[-1] in _TRAILING_CONNECTORS):
        tokens.pop()
    return " ".join(tokens)[:MAX_NAME_LENGTH].strip()


def normalize_company_name(raw: object) -> NormalizedName | None:
    """Return the display-cleaned name and a comparison key, or ``None`` for blank/punctuation.

    Display: Unicode NFKC, control and invisible characters removed, whitespace collapsed,
    cut to ``MAX_NAME_LENGTH``. Key: casefolded; Latin accents and Arabic diacritics/tatweel
    removed; alef variants folded to ``ا``, ``ى`` to ``ي``, ``ة`` to ``ه``; any decimal digits
    (including Arabic-Indic) become ASCII; ``&`` becomes ``and``; punctuation (Latin and
    Arabic) becomes a space; leading ``the``/``شركة`` and trailing legal forms (LLC, Ltd, Inc,
    Co., PJSC, FZE, ذ.م.م, ش.م.ع ...) are dropped, never the last remaining word.
    ``Acme Trading L.L.C.`` and ``ACME  trading, LLC`` share the key ``acme trading``.
    """
    try:
        if not isinstance(raw, str):
            return None
        warnings: list[str] = []
        display = _clean_display(raw[: MAX_NAME_LENGTH * 4])
        if len(display) > MAX_NAME_LENGTH or len(raw) > MAX_NAME_LENGTH * 4:
            display = display[:MAX_NAME_LENGTH].rstrip()
            warnings.append("name_truncated")
        key = name_key(display)
        if not display or not key:
            return None
        return NormalizedName(display, key, tuple(warnings))
    except (ValueError, UnicodeError):  # defensive: the contract is "never raise"
        return None


# ------------------------------------------------------------------------------ match keys


class MatchKey(NamedTuple):
    """One identity key. ``kind`` is ``domain``, ``profile`` or ``name``."""

    kind: Literal["domain", "profile", "name"]
    value: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


def company_match_keys(
    name: object = None, website: object = None, profile_url: object = None
) -> tuple[MatchKey, ...]:
    """Ordered identity keys for duplicate detection: domain, then profile, then name.

    * ``domain``: ``normalize_domain(website)``, exactly what ``Company.domain`` stores;
    * ``profile``: ``NormalizedProfileUrl.identity`` (``linkedin:acme``);
    * ``name``: the comparison key (weak: callers should add country before treating a name
      match as more than "possible duplicate").

    Missing or unusable parts are skipped, so the result may be empty. Keys are de-duplicated.
    """
    return normalize_company_input(name, website, profile_url).match_keys


@dataclass(frozen=True, slots=True)
class NormalizedCompanyInput:
    """Everything derived from one company row; ``warnings`` lists rejected parts."""

    name: NormalizedName | None
    website: NormalizedWebsite | None
    profile: NormalizedProfileUrl | None
    warnings: tuple[str, ...]
    match_keys: tuple[MatchKey, ...]

    @property
    def domain(self) -> str | None:
        return self.website.domain if self.website else None

    @property
    def profile_url_canonical(self) -> str | None:
        return self.profile.url if self.profile else None

    @property
    def name_normalized(self) -> str | None:
        return self.name.key if self.name else None


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def normalize_company_input(
    name: object = None, website: object = None, profile_url: object = None
) -> NormalizedCompanyInput:
    """Normalize one row's name, website and profile URL; unusable parts become warnings."""
    norm_name = normalize_company_name(name)
    site = normalize_website(website)
    profile = normalize_profile_url(profile_url)
    warnings: list[str] = []
    for label, raw, result in (
        ("name", name, norm_name),
        ("website", website, site),
        ("profile_url", profile_url, profile),
    ):
        if result is None and not _blank(raw):
            warnings.append(f"{label}_unparseable")
        elif result is not None:
            warnings.extend(f"{label}:{w}" for w in result.warnings)
    keys: list[MatchKey] = []
    if site is not None:
        keys.append(MatchKey("domain", site.domain))
    if profile is not None:
        keys.append(MatchKey("profile", profile.identity))
    if norm_name is not None:
        keys.append(MatchKey("name", norm_name.key))
    return NormalizedCompanyInput(
        norm_name, site, profile, tuple(warnings), tuple(dict.fromkeys(keys))
    )


# ----------------------------------------------------------------------------- classification

InputKind = Literal["empty", "url", "domain", "profile_url", "name", "invalid"]
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")


@dataclass(frozen=True, slots=True)
class ClassifiedInput:
    """``kind`` plus the canonical ``value`` for it (URL, domain, profile URL or display name)."""

    kind: InputKind
    value: str | None = None


def classify_input(raw: object) -> ClassifiedInput:
    """Decide what a single free-text box holds: URL, domain, profile URL or company name.

    * ``empty``: blank;
    * ``profile_url``: a recognised LinkedIn/Crunchbase profile (value: canonical URL);
    * ``url``: has a scheme, ``www.`` or a path/query after a domain (value: canonical https URL);
    * ``domain``: a bare ``example.com`` / ``acme.com.sa`` / ``بنك.السعودية`` (value: normalized);
    * ``invalid``: a ``javascript:``/``data:``/``mailto:``... scheme or a URL with credentials;
    * ``name``: everything else (value: display-cleaned name), e.g. ``Acme Trading LLC``.
    """
    try:
        return _classify_input(raw)
    except (ValueError, UnicodeError):  # defensive: the contract is "never raise"
        return ClassifiedInput("invalid")


def _classify_input(raw: object) -> ClassifiedInput:
    if not isinstance(raw, str) or not raw.strip():
        return ClassifiedInput("empty")
    text = raw.strip()
    scheme = _SCHEME.match(text)
    has_scheme = bool(scheme and scheme.group(2))
    if scheme and not has_scheme and not _PORT_AFTER_HOST.match(text[scheme.end() :]):
        return ClassifiedInput("invalid")  # "javascript:alert(1)", "mailto:a@b.co"
    explicit = has_scheme or text.startswith("//") or text.lower().startswith("www.")
    if explicit or not _FORBIDDEN_IN_URL.search(text):
        profile = normalize_profile_url(text)
        if profile is not None and profile.provider != "generic":
            return ClassifiedInput("profile_url", profile.url)
        site = normalize_website(text)
        if site is None:
            if has_scheme or text.startswith("//"):
                return ClassifiedInput("invalid")
        elif explicit or site.url != "https://" + site.domain:
            return ClassifiedInput("url", site.url)
        elif _TLD.match(site.domain.rsplit(".", 1)[-1]):
            return ClassifiedInput("domain", site.domain)
    name = normalize_company_name(text)
    if name is None:
        return ClassifiedInput("invalid")
    return ClassifiedInput("name", name.display)
