"""Content extraction for fetched pages, standard library only (issue #94).

``extract_page(html, base_url)`` never raises and always returns an ``ExtractedPage``.

* Main text: ``script``, ``style``, ``noscript``, ``template``, ``svg`` and ``iframe`` content,
  and anything marked hidden (``hidden`` attribute, ``aria-hidden="true"``, inline
  ``display:none``, ``visibility:hidden``, ``font-size:0``, ``opacity:0``) is dropped entirely.
  ``nav``, ``footer``, ``aside``, ``form``, ``select``, ``button`` and ``dialog`` are boilerplate:
  their text is dropped but their links are still collected (the menu is the best place to find
  the About and Careers pages). Headings are kept on their own line with a ``#`` prefix by level.
  Whitespace is collapsed, repeated lines removed, and the text capped at ``max_chars``.
* ``title``, meta ``description`` (or ``og:description``), ``canonical`` URL, ``lang``.
* Links: same-site only (same host, ``www.`` ignored), ``http``/``https``, no fragments,
  de-duplicated,
  capped, each with a short anchor text.
* JSON-LD: Organization-like objects reduced to ``name``, ``url``, ``sameAs``, ``address`` and
  ``foundingDate`` with size caps. They are hints, not facts.
* ``robots_directives``: directives from ``<meta name="robots">`` (or our own token).

Everything extracted is UNTRUSTED text written by a third party. A page can say "ignore previous
instructions" and we keep it verbatim as plain text. It is never executed, evaluated or parsed
as markup again. Wrap it with ``apps.integrations.llm.wrap_untrusted`` before it goes near a
prompt (docs/integrations/website-fetching.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_TITLE = 300
MAX_DESCRIPTION = 600
MAX_ANCHOR = 120
MAX_DEPTH = 400
MAX_ATTR = 2048
_MAX_ORGS = 5
_MAX_SAMEAS = 20
_MAX_FIELD = 300

_VOID = frozenset(
    ("area base br col embed hr img input link meta param source track wbr command keygen")
    .replace("\n", " ")
    .split(" ")
)
_DROP = frozenset(
    [
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "iframe",
        "object",
        "embed",
        "math",
    ]
)
_BOILERPLATE = frozenset(["nav", "footer", "aside", "form", "select", "option", "button", "dialog"])
_BLOCK = frozenset(
    [
        "address",
        "article",
        "blockquote",
        "body",
        "br",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "header",
        "hgroup",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    ]
)
_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])|opacity\s*:\s*0(?![.\d])",
    re.IGNORECASE,
)
_WS = re.compile("[ \t\r\f\v\u00a0\u200b\u200c\u200d\u2060\ufeff]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_LANG = re.compile(r"[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8}){0,3}")
_ORG_TYPES = frozenset(
    [
        "organization",
        "corporation",
        "ngo",
        "localbusiness",
        "educationalorganization",
        "governmentorganization",
        "medicalorganization",
        "sportsorganization",
        "onlinebusiness",
        "store",
    ]
)
_NOISE_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".zip",
    ".gz",
    ".tar",
    ".rar",
    ".7z",
    ".exe",
    ".dmg",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".rss",
    ".woff",
    ".woff2",
    ".ttf",
)
_CHARSET_META = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9_.:-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Link:
    url: str
    text: str


@dataclass(frozen=True)
class OrganizationHint:
    """Organization data a page declared about itself (schema.org JSON-LD). A hint to verify."""

    name: str = ""
    url: str = ""
    same_as: tuple[str, ...] = ()
    address: str = ""
    founding_date: str = ""


@dataclass(frozen=True)
class ExtractedPage:
    text: str = ""
    title: str = ""
    description: str = ""
    canonical_url: str = ""
    lang: str = ""
    links: tuple[Link, ...] = ()
    organizations: tuple[OrganizationHint, ...] = ()
    robots_directives: frozenset[str] = frozenset()
    truncated: bool = False
    warnings: tuple[str, ...] = field(default=())


def clean_text(value: object, limit: int = _MAX_FIELD) -> str:
    """One line of plain text: control characters removed, whitespace collapsed, capped."""
    if not isinstance(value, str):
        return ""
    return _WS.sub(" ", _CONTROL.sub("", value).replace("\n", " ")).strip()[:limit]


def site_key(host: str) -> str:
    """The host with one leading ``www.`` removed: our definition of 'same site'."""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def decode_body(data: bytes, content_type: str = "") -> str:
    """Bytes to text: charset from the header, else from a ``<meta charset>``, else UTF-8.
    Undecodable bytes become U+FFFD; NUL bytes are removed. Never raises."""
    charset = ""
    match = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)
    elif (sniff := _CHARSET_META.search(data[:4096])) is not None:
        charset = sniff.group(1).decode("ascii", "ignore")
    for candidate in (charset, "utf-8"):
        if not candidate:
            continue
        try:
            return data.decode(candidate, errors="replace").replace("\x00", "")
        except (LookupError, ValueError):
            continue
    return data.decode("utf-8", errors="replace").replace("\x00", "")


def parse_robots_directives(value: str) -> frozenset[str]:
    """Directives of a robots meta tag or an ``X-Robots-Tag`` header value, lower-case. A leading
    ``agent:`` prefix is honoured only for our own token or ``*``; other agents are ignored."""
    return frozenset(parse_directives_for(value, ()))


def parse_directives_for(value: str, agent_tokens: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for chunk in value.lower().split(","):
        item = chunk.strip()
        if ":" in item:
            prefix, _, rest = item.partition(":")
            prefix = prefix.strip()
            if " " not in prefix and prefix not in ("unavailable_after",):
                if prefix in agent_tokens or prefix == "*":
                    item = rest.strip()
                else:
                    continue
        if item and len(item) <= 40:
            result.extend(p for p in item.split() if p)
    return result[:50]


class _Extractor(HTMLParser):
    def __init__(self, base_url: str, max_chars: int, max_links: int, max_jsonld: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.base_site = site_key(urlsplit(base_url).hostname or "")
        self.max_chars = max_chars
        self.max_links = max_links
        self.max_jsonld = max_jsonld
        self.stack: list[tuple[str, int]] = []  # (tag, kind) kind 1 boilerplate, 2 dropped
        self.drop_count = 0
        self.boiler_count = 0
        self.parts: list[str] = []
        self.chars = 0
        self.overflow = False
        self.title = ""
        self.in_title = False
        self.description = ""
        self.og_description = ""
        self.canonical = ""
        self.lang = ""
        self.directives: set[str] = set()
        self.links: list[Link] = []
        self.seen_links: set[str] = set()
        self.link_url: str | None = None
        self.link_text: list[str] = []
        self.jsonld: list[str] = []
        self.jsonld_buf: list[str] | None = None
        self.jsonld_size = 0

    # -- helpers ---------------------------------------------------------------------------
    def _emit(self, text: str) -> None:
        if self.overflow:
            return
        self.parts.append(text)
        self.chars += len(text)
        if self.chars > self.max_chars * 3:  # raw text, before whitespace collapsing
            self.overflow = True

    def _hidden(self, attrs: dict[str, str]) -> bool:
        if "hidden" in attrs or attrs.get("aria-hidden", "").strip().lower() == "true":
            return True
        return bool(_HIDDEN_STYLE.search(attrs.get("style", "")))

    def _add_link(self, href: str, text: str) -> None:
        if len(self.links) >= self.max_links:
            return
        try:
            absolute = urljoin(self.base_url, href.strip())
            parts = urlsplit(absolute)
            host = parts.hostname or ""
            if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc:
                return
            if site_key(host) != self.base_site or parts.path.lower().endswith(_NOISE_EXTENSIONS):
                return
            clean = parts._replace(fragment="").geturl()
        except ValueError:
            return
        if clean in self.seen_links or len(clean) > 1000:
            return
        self.seen_links.add(clean)
        self.links.append(Link(clean, clean_text(text, MAX_ANCHOR)))

    # -- parser callbacks ------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "")[:MAX_ATTR] for k, v in attrs[:40]}
        if tag == "html" and not self.lang:
            lang = a.get("lang", "").strip()
            if _LANG.fullmatch(lang):
                self.lang = lang.lower()
        if tag == "meta":
            self._meta(a)
        elif tag == "link":
            if "canonical" in a.get("rel", "").lower().split() and not self.canonical:
                self.canonical = a.get("href", "").strip()
        elif tag == "title":
            self.in_title = True
        if tag == "a" and "href" in a and self.link_url is None and not self.drop_count:
            self.link_url = a["href"]
            self.link_text = []
        if (
            tag == "script"
            and a.get("type", "").lower().split(";")[0].strip() == "application/ld+json"
        ):
            self.jsonld_buf = []
        if tag in _VOID:
            if tag == "br":
                self._emit("\n")
            return
        kind = 0
        if tag in _DROP or self._hidden(a):
            kind = 2
        elif tag in _BOILERPLATE:
            kind = 1
        if len(self.stack) < MAX_DEPTH:
            self.stack.append((tag, kind))
            if kind == 2:
                self.drop_count += 1
            elif kind == 1:
                self.boiler_count += 1
        if not (self.drop_count or self.boiler_count):
            if tag in _HEADINGS:
                self._emit("\n" + _HEADINGS[tag])
            elif tag in _BLOCK:
                self._emit("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.link_url is not None:
            self._add_link(self.link_url, " ".join(self.link_text))
            self.link_url = None
        if tag == "script" and self.jsonld_buf is not None:
            self.jsonld.append("".join(self.jsonld_buf))
            self.jsonld_buf = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for _, kind in self.stack[index:]:
                    if kind == 2:
                        self.drop_count -= 1
                    elif kind == 1:
                        self.boiler_count -= 1
                del self.stack[index:]
                break
        if not (self.drop_count or self.boiler_count) and (tag in _HEADINGS or tag in _BLOCK):
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self.jsonld_buf is not None:
            if self.jsonld_size <= self.max_jsonld:
                self.jsonld_buf.append(data)
                self.jsonld_size += len(data)
            return
        if self.in_title:
            if len(self.title) < MAX_TITLE * 2:
                self.title += data
            return
        if self.link_url is not None and len(self.link_text) < 50 and not self.drop_count:
            self.link_text.append(data)
        if self.drop_count or self.boiler_count:
            return
        self._emit(data)

    def _meta(self, a: dict[str, str]) -> None:
        name = (a.get("name") or a.get("property") or "").strip().lower()
        content = a.get("content", "")
        if name == "description" and not self.description:
            self.description = content
        elif name == "og:description" and not self.og_description:
            self.og_description = content
        elif name in ("robots", "abmengine"):
            self.directives.update(parse_robots_directives(content))

    # -- output ----------------------------------------------------------------------------
    def build_text(self) -> tuple[str, bool]:
        lines: list[str] = []
        previous = ""
        for raw in "".join(self.parts).split("\n"):
            line = _WS.sub(" ", _CONTROL.sub("", raw)).strip()
            if not line or line == previous:
                continue
            lines.append(line)
            previous = line
        text = "\n".join(lines)
        truncated = self.overflow or len(text) > self.max_chars
        if len(text) > self.max_chars:
            text = text[: self.max_chars].rstrip()
        return text, truncated


def _scalar(value: object) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("@id") or "")
    return ""


def _address(value: object) -> str:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        keys = ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
        pieces = [_scalar(value.get(k)) for k in keys]
        return clean_text(", ".join(p for p in pieces if p))
    return ""


def _org_types(node: dict[str, object]) -> bool:
    raw = node.get("@type")
    types = raw if isinstance(raw, list) else [raw]
    return any(isinstance(t, str) and t.rsplit("/", 1)[-1].lower() in _ORG_TYPES for t in types)


def parse_jsonld(blocks: list[str]) -> tuple[OrganizationHint, ...]:
    """Organization hints from JSON-LD script bodies. Malformed or oversized JSON is skipped."""
    found: list[OrganizationHint] = []
    budget = 500
    for block in blocks[:10]:
        try:
            data = json.loads(block)
        except (ValueError, RecursionError):
            continue
        todo: list[tuple[object, int]] = [(data, 0)]
        while todo and budget > 0 and len(found) < _MAX_ORGS:
            node, depth = todo.pop()
            budget -= 1
            if isinstance(node, list):
                todo.extend((n, depth + 1) for n in node[:50] if depth < 6)
            elif isinstance(node, dict):
                if _org_types(node):
                    same = node.get("sameAs")
                    urls = (
                        [same] if isinstance(same, str) else same if isinstance(same, list) else []
                    )
                    clean_urls = tuple(
                        u.strip()[:500]
                        for u in urls[:_MAX_SAMEAS]
                        if isinstance(u, str)
                        and u.strip().lower().startswith(("http://", "https://"))
                    )
                    found.append(
                        OrganizationHint(
                            name=_scalar(node.get("name")),
                            url=_scalar(node.get("url")),
                            same_as=clean_urls,
                            address=_address(node.get("address")),
                            founding_date=_scalar(node.get("foundingDate")),
                        )
                    )
                graph = node.get("@graph")
                if isinstance(graph, list) and depth < 6:
                    todo.extend((n, depth + 1) for n in graph[:50])
    return tuple(found)


def extract_page(
    html: str,
    base_url: str,
    *,
    max_chars: int = 20_000,
    max_links: int = 50,
    max_jsonld_bytes: int = 20_000,
) -> ExtractedPage:
    """Extract text, metadata, same-site links and JSON-LD hints. Never raises."""
    parser = _Extractor(base_url, max_chars, max_links, max_jsonld_bytes)
    warnings: list[str] = []
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # hostile markup must never break a run; keep what we have
        warnings.append("html_parse_error")
    try:
        text, truncated = parser.build_text()
        organizations = parse_jsonld(parser.jsonld)
    except Exception:
        text, truncated, organizations = "", False, ()
        warnings.append("extract_error")
    canonical = ""
    if parser.canonical:
        try:
            candidate = urljoin(base_url, parser.canonical)
            if urlsplit(candidate).scheme in ("http", "https") and len(candidate) <= 1000:
                canonical = candidate
        except ValueError:
            canonical = ""
    return ExtractedPage(
        text=text,
        title=clean_text(parser.title, MAX_TITLE),
        description=clean_text(parser.description or parser.og_description, MAX_DESCRIPTION),
        canonical_url=canonical,
        lang=parser.lang,
        links=tuple(parser.links),
        organizations=organizations,
        robots_directives=frozenset(parser.directives),
        truncated=truncated,
        warnings=tuple(warnings),
    )
