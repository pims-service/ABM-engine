"""HTTP transport for provider adapters that call a JSON API (issue #81).

``Transport`` is the seam: the adapter builds an ``HttpRequest`` and gets an ``HttpResponse``;
tests inject a fake, so no test touches the network. ``UrllibTransport`` is the real one, on the
standard library (``http.client``; no HTTP dependency is added). It enforces:

* ``https`` only, a host from the allow list, no credentials in the URL, port 443 only;
* a TLS context with certificate and host name verification (``ssl.create_default_context``);
* a connect/read timeout on every request;
* no redirects (a 3xx is returned as is, never followed, so a key cannot be sent elsewhere);
* a response size cap (the body is read in bounded chunks; over the cap is an error);
* no logging of URLs with query, headers or bodies, and no upstream text in errors.
"""

from __future__ import annotations

import http.client
import ssl
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from apps.integrations.framework import (
    ProviderBadRequest,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUpstreamError,
)

DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class HttpRequest:
    """One request. ``headers`` (it holds the API key) and ``body`` are kept out of ``repr``."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes | None = field(default=None, repr=False)
    timeout: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class HttpResponse:
    """A response; header names are lower case. ``body`` is kept out of ``repr``."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes = field(default=b"", repr=False)


class Transport(Protocol):
    """Sends one request. Raises only ``ProviderError`` subclasses."""

    def send(self, request: HttpRequest) -> HttpResponse: ...


def check_url(url: str, allowed_hosts: Collection[str]) -> tuple[str, str]:
    """Validate ``url`` and return ``(host, path_and_query)``. Raises ``ProviderBadRequest``."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        raise ProviderBadRequest("api_base") from None
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or host not in {h.lower() for h in allowed_hosts}
    ):
        raise ProviderBadRequest("api_base")
    path = parts.path or "/"
    return host, f"{path}?{parts.query}" if parts.query else path


class UrllibTransport:
    """The real transport (standard library). Construct with the adapter's allowed hosts."""

    def __init__(
        self,
        allowed_hosts: Collection[str],
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        provider_name: str = "",
        context: ssl.SSLContext | None = None,
    ) -> None:
        self._allowed_hosts = tuple(allowed_hosts)
        self._max_bytes = max_response_bytes
        self._provider = provider_name
        self._context = context

    def send(self, request: HttpRequest) -> HttpResponse:
        host, target = check_url(request.url, self._allowed_hosts)
        context = self._context or ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, 443, timeout=request.timeout, context=context)
        try:
            conn.request(request.method, target, body=request.body, headers=dict(request.headers))
            raw = conn.getresponse()
            body = self._read_capped(raw)
            headers = {k.lower(): v for k, v in raw.getheaders()}
            return HttpResponse(raw.status, headers, body)
        except TimeoutError:
            raise ProviderTimeout(provider_name=self._provider) from None
        except (OSError, http.client.HTTPException):
            # Includes ssl.SSLError and connection errors; their text may name hosts, so it is
            # not kept.
            raise ProviderUnavailable(provider_name=self._provider) from None
        finally:
            conn.close()

    def _read_capped(self, raw: http.client.HTTPResponse) -> bytes:
        declared = raw.getheader("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            raise ProviderUpstreamError(provider_name=self._provider)
        body = raw.read(self._max_bytes + 1)
        if len(body) > self._max_bytes:
            raise ProviderUpstreamError(provider_name=self._provider)
        return body
