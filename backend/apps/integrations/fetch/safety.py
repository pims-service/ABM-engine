"""URL and address safety for the website fetcher (issue #94): the SSRF guard and the denylist.

Rules, all applied BEFORE any connection and again on every redirect hop:

* only ``http`` and ``https``; no userinfo (``user:pass@host``); only the allowed ports (80, 443);
* the host must be a DNS name (IDNA/punycode normalised) or a canonical IP literal. Numeric
  shortcuts such as ``2130706433``, ``0x7f.1`` or ``017700000001`` are refused outright, because
  the system resolver (inet_aton) would turn them into ``127.0.0.1``;
* we resolve the name ourselves and every returned address must be public: loopback, private,
  link-local (cloud metadata ``169.254.169.254``), CGNAT ``100.64/10``, multicast, reserved,
  documentation, unspecified, and the IPv6 equivalents (``::1``, ``fc00::/7``, ``fe80::/10``,
  IPv4-mapped ``::ffff:a.b.c.d`` judged by the embedded IPv4, 6to4, NAT64, Teredo). One bad
  answer rejects the whole name (no cherry picking). The transport then connects to the
  validated address, not to the name, so a second DNS answer cannot redirect the connection
  (DNS rebinding).
* the denylist (``config.denied_hosts``) is matched on whole labels: ``sub.linkedin.com`` is
  blocked, ``evil-linkedin.com`` and ``notlinkedin.com`` are not.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from . import config
from .errors import (
    REASON_HOST,
    REASON_NO_ADDRESS,
    REASON_PORT,
    REASON_PRIVATE_ADDRESS,
    REASON_SCHEME,
    REASON_USERINFO,
    FetchBlockedByPolicy,
    FetchBlockedUnsafeTarget,
    FetchInvalidUrl,
    FetchNetworkError,
)

MAX_URL_LENGTH = 2048

#: ``resolver(host, port)`` returns the IP address strings of ``host``. Injectable for tests.
Resolver = Callable[[str, int], Sequence[str]]

_BAD_CHARS = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_HOST_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?")
_NUMERIC_LABEL = re.compile(r"(0x[0-9a-f]*|[0-9]+)")
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = "/?%:@!$&'()*+,;=-._~[]"

_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, cloud metadata
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, broadcast
        "::/128",
        "::1/128",
        "::/96",  # IPv4-compatible (deprecated)
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",
        "100::/64",  # discard
        "2001::/32",  # Teredo
        "2001:db8::/32",
        "2002::/16",  # 6to4
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local (deprecated)
        "ff00::/8",  # multicast
    )
)


@dataclass(frozen=True)
class Target:
    """A validated, normalised request target. Never carries userinfo or a fragment."""

    scheme: str
    host: str  # lower-case ASCII (punycode) name or IP literal without brackets
    port: int
    path: str  # path plus query, always starts with "/"

    @property
    def default_port(self) -> bool:
        return self.port == (443 if self.scheme == "https" else 80)

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.default_port else f"{host}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.authority}{self.path}"

    @property
    def origin(self) -> tuple[str, str, int]:
        return (self.scheme, self.host, self.port)


def ip_is_public(address: str) -> bool:
    """True only for a globally routable unicast address (IPv4-mapped IPv6 uses the IPv4)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False
    return not any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version)


def _normalise_host(raw: str) -> str:
    host = raw.strip().rstrip(".").lower()
    if not host or "%" in host:
        raise FetchBlockedUnsafeTarget(reason=REASON_HOST)
    if ":" in host:  # IPv6 literal
        try:
            return str(ipaddress.IPv6Address(host))
        except ValueError:
            raise FetchBlockedUnsafeTarget(reason=REASON_HOST) from None
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        raise FetchInvalidUrl(reason="bad_host") from None
    ascii_host = ascii_host.lower()
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise FetchBlockedUnsafeTarget(reason=REASON_HOST)
    if all(_NUMERIC_LABEL.fullmatch(label) for label in labels) or _NUMERIC_LABEL.fullmatch(
        labels[-1]
    ):
        # Only a canonical dotted quad is an IP literal; every other numeric form is refused.
        try:
            return str(ipaddress.IPv4Address(ascii_host))
        except ValueError:
            raise FetchBlockedUnsafeTarget(reason=REASON_HOST) from None
    return ascii_host


def parse_target(url: object, *, allowed_ports: Sequence[int] | None = None) -> Target:
    """Validate ``url`` and return a ``Target``. Raises ``FetchInvalidUrl`` for garbage and
    ``FetchBlockedUnsafeTarget`` for a scheme, userinfo, port or host the fetcher refuses."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise FetchInvalidUrl(reason="bad_url")
    text = url.strip()
    if _BAD_CHARS.search(text):
        raise FetchInvalidUrl(reason="bad_characters")
    try:
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise FetchBlockedUnsafeTarget(reason=REASON_SCHEME)
        if not parts.netloc:
            raise FetchInvalidUrl(reason="no_host")
        if "@" in parts.netloc:
            raise FetchBlockedUnsafeTarget(reason=REASON_USERINFO)
        port = parts.port
        raw_host = parts.hostname
    except ValueError:
        raise FetchInvalidUrl(reason="bad_url") from None
    if not raw_host:
        raise FetchInvalidUrl(reason="no_host")
    host = _normalise_host(raw_host)
    port = port if port is not None else (443 if scheme == "https" else 80)
    ports = (
        tuple(allowed_ports) if allowed_ports is not None else tuple(config.get("allowed_ports"))
    )
    if port not in ports:
        raise FetchBlockedUnsafeTarget(reason=REASON_PORT, host=_safe_host(host))
    path = quote(parts.path or "/", safe=_PATH_SAFE)
    if not path.startswith("/"):
        path = "/" + path
    if parts.query:
        path += "?" + quote(parts.query, safe=_QUERY_SAFE)
    return Target(scheme=scheme, host=host, port=port, path=path)


def _safe_host(host: str) -> str:
    return host if re.fullmatch(r"[a-z0-9.-]{1,253}", host) else ""


def is_denied_host(host: str, denied: Sequence[str] | None = None) -> bool:
    """Whole-label suffix match: ``a.linkedin.com`` and ``linkedin.com`` yes, ``evil-linkedin.com``
    no."""
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (denied or config.denied_hosts()))


def check_policy(target: Target) -> None:
    """Raise ``FetchBlockedByPolicy`` for a denylisted host (Brief §4)."""
    if is_denied_host(target.host):
        raise FetchBlockedByPolicy(reason="denylisted_host", host=_safe_host(target.host))


def system_resolver(host: str, port: int) -> list[str]:
    """Resolve with the operating system (``getaddrinfo``), TCP only, addresses in answer order."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return seen


def resolve_public(target: Target, resolver: Resolver) -> tuple[str, ...]:
    """The validated addresses of ``target`` (an IP literal resolves to itself). Raises
    ``FetchBlockedUnsafeTarget`` if any address is not public, ``FetchNetworkError`` when the
    name does not resolve."""
    host = _safe_host(target.host)
    try:
        ipaddress.ip_address(target.host)
        answers: Sequence[str] = [target.host]
    except ValueError:
        try:
            answers = list(resolver(target.host, target.port))
        except (OSError, UnicodeError):
            raise FetchNetworkError(reason=REASON_NO_ADDRESS, host=host) from None
    if not answers:
        raise FetchNetworkError(reason=REASON_NO_ADDRESS, host=host)
    addresses = tuple(str(a).split("%", 1)[0] for a in answers[:32])
    if not all(ip_is_public(a) for a in addresses):
        raise FetchBlockedUnsafeTarget(reason=REASON_PRIVATE_ADDRESS, host=host)
    return addresses
