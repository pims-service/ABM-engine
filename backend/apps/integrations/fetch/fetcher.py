"""``PublicWebFetcher``: fetch one public web page safely and politely (issue #94).

Order of checks for every URL and every redirect hop (see ``safety.py`` and ``robots.py``):

1. syntax: http/https only, no userinfo, allowed ports, no numeric host tricks;
2. denylist (``FetchBlockedByPolicy``, Brief §4);
3. DNS resolved by us, every address must be public (``FetchBlockedUnsafeTarget``); the
   connection goes to those validated addresses only, the name is kept for TLS and ``Host``;
4. ``robots.txt`` of the host (cached): 4xx means allowed, 5xx or unreachable means disallowed;
5. per-host rate limit (``min_delay`` and the site's ``Crawl-delay``);
6. the request: honest User-Agent, no cookies, no credentials, size, time and decompression caps,
   content-type allowlist;
7. opt-out: ``X-Robots-Tag`` or ``<meta name="robots">`` with ``noai``/``noindex``/``none``
   makes us discard the content (``FetchOptedOut``).

The fetcher is stateless apart from its robots cache; it persists nothing. Register nothing at
import time.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Lock
from urllib.parse import urljoin

from apps.integrations.framework.errors import parse_retry_after
from apps.integrations.resilience import config as resilience_config
from apps.integrations.resilience.timeouts import TimeoutPolicy

from . import config
from .errors import (
    REASON_REDIRECT,
    REASON_TOO_MANY_REDIRECTS,
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
from .extract import (
    ExtractedPage,
    Link,
    OrganizationHint,
    clean_text,
    decode_body,
    extract_page,
    parse_directives_for,
)
from .politeness import HostRateLimiter, WaitTooLong, shared_limiter
from .robots import RobotsRules, agent_token, parse_robots
from .safety import Resolver, Target, check_policy, parse_target, resolve_public, system_resolver
from .transport import (
    StdlibTransport,
    Transport,
    TransportError,
    TransportRequest,
    TransportResponse,
    header_value,
    header_values,
)

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: Directives that make us discard the page (documented in docs/integrations/website-fetching.md).
OPT_OUT_DIRECTIVES = frozenset({"noai", "noindex", "none", "notrain"})

ROBOTS_ALLOWED = "allowed"
ROBOTS_NO_FILE = "no_robots_txt"
ROBOTS_DISALLOWED = "disallowed"
ROBOTS_UNREACHABLE = "robots_unreachable"

_PURPOSE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}")
_PLAIN_LINE = re.compile("[ \t\r\f\v\u00a0]+")


@dataclass(frozen=True)
class RobotsDecision:
    """What robots.txt said about the final URL. ``reason`` is one of ``allowed``,
    ``no_robots_txt`` (4xx: no rules), ``disallowed`` and ``robots_unreachable`` (5xx, timeout)."""

    allowed: bool
    reason: str
    robots_url: str
    checked_at: datetime
    crawl_delay: float | None = None


@dataclass(frozen=True)
class FetchResult:
    """A fetched page. ``text`` and every other extracted field are UNTRUSTED third-party data."""

    url: str  # the URL asked for (normalised)
    final_url: str  # after redirects
    status: int
    content_type: str  # media type only, lower-case
    text: str
    title: str
    description: str
    canonical_url: str
    lang: str  # language hint: the page's lang attribute, else Content-Language, else ""
    links: tuple[Link, ...]  # same-site only, capped
    organizations: tuple[OrganizationHint, ...]  # JSON-LD hints, to be verified
    fetched_at: datetime  # timezone-aware (UTC)
    bytes_read: int  # bytes received from the network for this call (robots.txt included)
    decoded_bytes: int  # size of the page body after decompression
    redirect_chain: tuple[str, ...]  # URLs left through redirects, in order (final not included)
    robots: RobotsDecision
    truncated: bool  # text was cut at the character cap
    content_hash: str  # "sha256:<hex>" of the decoded page body
    purpose: str
    warnings: tuple[str, ...] = field(default=())

    def provenance(self) -> dict[str, str]:
        """The evidence record for this page: cite it next to anything derived from ``text``."""
        return {
            "source_url": self.final_url,
            "retrieved_at": self.fetched_at.isoformat(),
            "content_hash": self.content_hash,
            "robots": self.robots.reason,
            "purpose": self.purpose,
        }


@dataclass
class _Context:
    deadline: float
    bytes_read: int = 0
    dns: dict[tuple[str, int], tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Raw:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    wire_bytes: int
    truncated: bool
    final: Target
    chain: tuple[str, ...]


@dataclass
class _RobotsEntry:
    rules: RobotsRules | None  # None: unreachable
    reason: str
    expires: float


class PublicWebFetcher:
    """Fetch public pages. Construct once, reuse (the robots cache and the limiter live here).

    ``transport``, ``resolver``, ``clock``, ``sleep`` and ``now`` are injectable so tests run
    with no network and no waiting."""

    def __init__(
        self,
        transport: Transport | None = None,
        resolver: Resolver | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        limiter: HostRateLimiter | None = None,
        timeouts: TimeoutPolicy | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._transport: Transport = transport or StdlibTransport()
        self._resolver: Resolver = resolver or system_resolver
        self._clock = clock
        self._now = now or (lambda: datetime.now(UTC))
        if limiter is None:
            default_clock = clock is time.monotonic and sleep is time.sleep
            limiter = (
                shared_limiter() if default_clock else HostRateLimiter(clock=clock, sleep=sleep)
            )
        self._limiter = limiter
        self._timeouts = timeouts or TimeoutPolicy.from_settings()
        self._user_agent = user_agent or resilience_config.user_agent()
        self._token = agent_token(self._user_agent).lower()
        self._robots: dict[tuple[str, str, int], _RobotsEntry] = {}
        self._robots_lock = Lock()

    @property
    def user_agent(self) -> str:
        return self._user_agent

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    # ------------------------------------------------------------------ public API
    def fetch(self, url: str, *, purpose: str) -> FetchResult:
        """Fetch ``url`` and return the extracted page, or raise a ``FetchError``.

        ``purpose`` is a short machine label ("company_profile") kept in the result and the logs.
        Raises ``ValueError`` for a malformed purpose (a bug of the caller)."""
        if not isinstance(purpose, str) or not _PURPOSE.fullmatch(purpose):
            raise ValueError("purpose must be a short label of [a-z0-9_.:-]")
        start = parse_target(url)
        total = float(config.get("total_deadline"))
        if self._timeouts.deadline:
            total = min(total, float(self._timeouts.deadline))
        ctx = _Context(deadline=self._clock() + total)
        try:
            result = self._fetch(start, ctx, purpose)
        except FetchError as exc:
            logger.info(
                "website fetch failed host=%s purpose=%s code=%s", start.host, purpose, exc.code
            )
            raise
        logger.info(
            "website fetch ok host=%s purpose=%s status=%s bytes=%s",
            start.host,
            purpose,
            result.status,
            result.bytes_read,
        )
        return result

    # ------------------------------------------------------------------ pipeline
    def _fetch(self, start: Target, ctx: _Context, purpose: str) -> FetchResult:
        raw, decision = self._get_page(start, ctx)
        final = raw.final
        mime = header_value(raw.headers, "content-type").split(";", 1)[0].strip().lower()
        text_body = decode_body(raw.body, header_value(raw.headers, "content-type"))
        max_chars = int(config.get("max_text_chars"))
        if mime == "text/plain":
            page = _plain_page(text_body, max_chars)
        else:
            page = extract_page(
                text_body,
                final.url,
                max_chars=max_chars,
                max_links=int(config.get("max_links")),
                max_jsonld_bytes=int(config.get("max_jsonld_bytes")),
            )
        if OPT_OUT_DIRECTIVES & page.robots_directives:
            raise FetchOptedOut(reason="meta_robots", host=final.host)
        lang = page.lang or _language(header_value(raw.headers, "content-language"))
        return FetchResult(
            url=start.url,
            final_url=final.url,
            status=raw.status,
            content_type=mime,
            text=page.text,
            title=page.title,
            description=page.description,
            canonical_url=page.canonical_url,
            lang=lang,
            links=page.links,
            organizations=page.organizations,
            fetched_at=self._now(),
            bytes_read=ctx.bytes_read,
            decoded_bytes=len(raw.body),
            redirect_chain=raw.chain,
            robots=decision,
            truncated=page.truncated,
            content_hash="sha256:" + hashlib.sha256(raw.body).hexdigest(),
            purpose=purpose,
            warnings=page.warnings,
        )

    def _get_page(self, start: Target, ctx: _Context) -> tuple[_Raw, RobotsDecision]:
        """Follow redirects, checking policy, SSRF rules and robots.txt at every hop."""
        max_redirects = int(config.get("max_redirects"))
        target = start
        chain: list[str] = []
        for hop in range(max_redirects + 1):
            check_policy(target)
            self._resolve(target, ctx)  # SSRF verdict first, so it is never masked by robots
            decision = self._robots_decision(target, ctx)
            if not decision.allowed:
                raise FetchBlockedByRobots(reason=decision.reason, host=target.host)
            delay = max(float(config.get("min_delay")), self._capped_delay(decision.crawl_delay))
            raw = self._request(target, ctx, delay=delay)
            if raw.status in REDIRECT_STATUSES:
                if hop == max_redirects:
                    raise FetchBlockedUnsafeTarget(
                        reason=REASON_TOO_MANY_REDIRECTS, host=target.host
                    )
                chain.append(target.url)
                target = self._redirect_target(target, raw.headers)
                continue
            return replace(raw, chain=tuple(chain)), decision
        raise FetchBlockedUnsafeTarget(reason=REASON_TOO_MANY_REDIRECTS)  # pragma: no cover

    # ------------------------------------------------------------------ one request
    def _request(
        self,
        target: Target,
        ctx: _Context,
        *,
        delay: float,
        robots: bool = False,
    ) -> _Raw:
        """One hop. A page hop checks status, redirects (returned unread), content type, opt-out
        header and sizes; a robots hop returns any answer and truncates oversize bodies."""
        check_policy(target)
        addresses = self._resolve(target, ctx)
        remaining = self._remaining(ctx)
        try:
            self._limiter.acquire(target.host, delay, max_wait=remaining)
        except WaitTooLong:
            raise FetchTimeout(reason="rate_limit_wait", host=target.host) from None
        remaining = self._remaining(ctx)
        request = TransportRequest(
            scheme=target.scheme,
            host=target.host,
            port=target.port,
            path=target.path,
            addresses=addresses,
            headers={
                "Host": target.authority,
                "User-Agent": self._user_agent,
                "Accept": "text/plain, */*;q=0.1"
                if robots
                else "text/html, application/xhtml+xml, text/plain;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "close",
            },
            connect_timeout=min(self._timeouts.connect, remaining),
            read_timeout=min(self._timeouts.read, remaining),
        )
        try:
            response = self._transport.open(request)
        except TransportError as exc:
            raise self._network_error(exc, target) from None
        try:
            return self._read_response(response, target, ctx, robots=robots)
        except TransportError as exc:
            raise self._network_error(exc, target) from None
        finally:
            response.close()

    def _read_response(
        self, response: TransportResponse, target: Target, ctx: _Context, *, robots: bool
    ) -> _Raw:
        status = int(response.status)
        headers = tuple(response.headers)
        no_body = _Raw(status, headers, b"", 0, False, target, ())
        if robots:
            if status != 200:
                return no_body
            max_decoded = int(config.get("robots_max_bytes"))
            max_wire = max_decoded
        else:
            if status in REDIRECT_STATUSES:
                if not header_value(headers, "location"):
                    raise FetchHttpError(status, host=target.host)
                return no_body
            if not 200 <= status < 300:
                raise FetchHttpError(
                    status,
                    host=target.host,
                    retry_after=parse_retry_after(header_value(headers, "retry-after")),
                )
            mime = header_value(headers, "content-type").split(";", 1)[0].strip().lower()
            if status in (204, 205) or mime not in ALLOWED_CONTENT_TYPES:
                raise FetchUnsupportedContent(reason="content_type", host=target.host)
            if self._header_opt_out(headers):
                raise FetchOptedOut(reason="x_robots_tag", host=target.host)
            max_decoded = int(config.get("max_bytes"))
            max_wire = int(config.get("max_compressed_bytes"))
            declared = header_value(headers, "content-length")
            if declared.isdigit() and int(declared) > max_wire:
                raise FetchTooLarge(reason="content_length", host=target.host)
        body, wire, truncated = self._read_body(
            response, headers, target, ctx, max_decoded, max_wire, truncate=robots
        )
        return _Raw(status, headers, body, wire, truncated, target, ())

    def _read_body(
        self,
        response: TransportResponse,
        headers: tuple[tuple[str, str], ...],
        target: Target,
        ctx: _Context,
        max_decoded: int,
        max_wire: int,
        *,
        truncate: bool,
    ) -> tuple[bytes, int, bool]:
        encoding = header_value(headers, "content-encoding").lower()
        wbits: int | None
        if encoding in ("", "identity"):
            wbits = None
        elif encoding in ("gzip", "x-gzip"):
            wbits = 16 + zlib.MAX_WBITS
        elif encoding == "deflate":
            wbits = zlib.MAX_WBITS
        else:
            raise FetchUnsupportedContent(reason="content_encoding", host=target.host)
        inflater = zlib.decompressobj(wbits) if wbits is not None else None
        out = bytearray()
        wire = 0
        first = True
        for chunk in response.iter_body():
            if self._clock() > ctx.deadline:
                raise FetchTimeout(reason="deadline", host=target.host)
            wire += len(chunk)
            ctx.bytes_read += len(chunk)
            if wire > max_wire:
                if truncate:
                    return bytes(out), wire, True
                raise FetchTooLarge(reason="wire_size", host=target.host)
            data = chunk
            if inflater is not None:
                room = max_decoded - len(out)
                try:
                    try:
                        data = inflater.decompress(chunk, room + 1)
                    except zlib.error:
                        if not (first and wbits == zlib.MAX_WBITS):
                            raise
                        inflater = zlib.decompressobj(-zlib.MAX_WBITS)  # raw deflate
                        data = inflater.decompress(chunk, room + 1)
                except zlib.error:
                    raise FetchUnsupportedContent(reason="bad_encoding", host=target.host) from None
            first = False
            room = max_decoded - len(out)
            if len(data) > room:
                if truncate:
                    out += data[:room]
                    return bytes(out), wire, True
                raise FetchTooLarge(reason="decoded_size", host=target.host)
            out += data
        return bytes(out), wire, False

    # ------------------------------------------------------------------ helpers
    def _remaining(self, ctx: _Context) -> float:
        remaining = ctx.deadline - self._clock()
        if remaining <= 0:
            raise FetchTimeout(reason="deadline")
        return remaining

    def _network_error(self, exc: TransportError, target: Target) -> FetchError:
        if exc.timeout:
            return FetchTimeout(reason="network_timeout", host=target.host)
        return FetchNetworkError(reason="network_error", host=target.host)

    def _resolve(self, target: Target, ctx: _Context) -> tuple[str, ...]:
        """Validated addresses of ``target``; one DNS lookup per host and port per fetch."""
        key = (target.host, target.port)
        if key not in ctx.dns:
            ctx.dns[key] = resolve_public(target, self._resolver)
        return ctx.dns[key]

    def _redirect_target(self, current: Target, headers: tuple[tuple[str, str], ...]) -> Target:
        location = header_value(headers, "location")
        try:
            nxt = parse_target(urljoin(current.url, location))
        except FetchInvalidUrl:
            raise FetchBlockedUnsafeTarget(reason=REASON_REDIRECT, host=current.host) from None
        downgrade = current.scheme == "https" and nxt.scheme == "http"
        if downgrade or not (nxt.port == current.port or nxt.default_port):
            raise FetchBlockedUnsafeTarget(reason=REASON_REDIRECT, host=current.host)
        return nxt

    def _header_opt_out(self, headers: tuple[tuple[str, str], ...]) -> bool:
        for value in header_values(headers, "x-robots-tag"):
            if OPT_OUT_DIRECTIVES & set(parse_directives_for(value, (self._token,))):
                return True
        return False

    @staticmethod
    def _capped_delay(crawl_delay: float | None) -> float:
        if crawl_delay is None:
            return 0.0
        return min(crawl_delay, float(config.get("max_crawl_delay")))

    # ------------------------------------------------------------------ robots.txt
    def _robots_decision(self, target: Target, ctx: _Context) -> RobotsDecision:
        robots_target = Target(target.scheme, target.host, target.port, "/robots.txt")
        entry = self._robots_entry(robots_target, ctx)
        now = self._now()
        if entry.rules is None:
            return RobotsDecision(False, ROBOTS_UNREACHABLE, robots_target.url, now)
        delay = entry.rules.crawl_delay
        if not entry.rules.allows(target.path):
            return RobotsDecision(False, ROBOTS_DISALLOWED, robots_target.url, now, delay)
        reason = ROBOTS_NO_FILE if entry.reason == ROBOTS_NO_FILE else ROBOTS_ALLOWED
        return RobotsDecision(True, reason, robots_target.url, now, delay)

    def _robots_entry(self, robots_target: Target, ctx: _Context) -> _RobotsEntry:
        key = robots_target.origin
        with self._robots_lock:
            cached = self._robots.get(key)
            if cached is not None and cached.expires > self._clock():
                return cached
        entry = self._load_robots(robots_target, ctx)
        with self._robots_lock:
            if len(self._robots) >= 1000:
                self._robots.pop(next(iter(self._robots)))
            self._robots[key] = entry
        return entry

    def _load_robots(self, robots_target: Target, ctx: _Context) -> _RobotsEntry:
        ttl = float(config.get("robots_ttl"))
        failure_ttl = float(config.get("robots_failure_ttl"))
        now = self._clock()
        try:
            raw = self._robots_get(robots_target, ctx)
        except FetchTimeout:
            if self._clock() > ctx.deadline:
                raise  # our own deadline ran out; not the site's fault, do not cache
            return _RobotsEntry(None, ROBOTS_UNREACHABLE, now + failure_ttl)
        except FetchError:
            return _RobotsEntry(None, ROBOTS_UNREACHABLE, now + failure_ttl)
        if raw.status == 200:
            text = raw.body.decode("utf-8", errors="replace")
            return _RobotsEntry(parse_robots(text, self._token), ROBOTS_ALLOWED, now + ttl)
        if 400 <= raw.status < 500:  # RFC 9309 2.3.1.3: "unavailable", crawl anything
            return _RobotsEntry(RobotsRules(), ROBOTS_NO_FILE, now + ttl)
        return _RobotsEntry(None, ROBOTS_UNREACHABLE, now + failure_ttl)  # 5xx, 429, odd 3xx

    def _robots_get(self, robots_target: Target, ctx: _Context) -> _Raw:
        """GET robots.txt following up to ``max_redirects`` hops, each one fully validated."""
        target = robots_target
        for hop in range(int(config.get("max_redirects")) + 1):
            raw = self._request(target, ctx, delay=float(config.get("min_delay")), robots=True)
            if raw.status not in REDIRECT_STATUSES:
                return raw
            if not header_value(raw.headers, "location") or hop >= int(config.get("max_redirects")):
                return _Raw(502, raw.headers, b"", 0, False, target, ())
            target = self._redirect_target(target, raw.headers)
        raise FetchHttpError(502)  # pragma: no cover


def _language(value: str) -> str:
    first = value.split(",", 1)[0].strip().lower()
    return first if re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8}){0,3}", first) else ""


def _plain_page(text: str, max_chars: int) -> ExtractedPage:
    lines: list[str] = []
    previous = ""
    for raw in text.split("\n"):
        line = clean_text(_PLAIN_LINE.sub(" ", raw), 2000)
        if line and line != previous:
            lines.append(line)
            previous = line
    joined = "\n".join(lines)
    return ExtractedPage(text=joined[:max_chars].rstrip(), truncated=len(joined) > max_chars)
