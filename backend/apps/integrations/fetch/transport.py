"""The HTTP transport of the website fetcher: a small protocol and a standard library client.

The fetcher never opens sockets itself. It builds a ``TransportRequest`` that already carries the
VALIDATED addresses and asks a ``Transport`` to open it, so tests use a fake transport and never
touch the network.

``StdlibTransport`` connects to the validated IP address (not the name, so DNS cannot change
between our check and the connection) and keeps the real host name for TLS (SNI and certificate
verification, always on) and for the ``Host`` header. It sends no cookies and no credentials,
reads no more than asked, and does not follow redirects (the fetcher validates every hop).
"""

from __future__ import annotations

import http.client
import socket
import ssl
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


class TransportError(Exception):
    """Connection level failure. ``timeout`` is True for connect/read timeouts."""

    def __init__(self, message: str = "transport error", *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


@dataclass(frozen=True)
class TransportRequest:
    scheme: str
    host: str  # name for SNI, certificate check and Host header
    port: int
    path: str  # path and query
    addresses: tuple[str, ...]  # validated IPs to connect to, in preference order
    headers: Mapping[str, str]
    connect_timeout: float
    read_timeout: float


class TransportResponse(Protocol):
    # Read-only on purpose: the fetcher only reads them, and read-only protocol members accept
    # any compatible type (a tuple of pairs for ``headers``, for example).
    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> Sequence[tuple[str, str]]: ...

    def iter_body(self, chunk_size: int = 16384) -> Iterator[bytes]:
        """The raw (still content-encoded) body in chunks. Stops when the consumer stops."""
        ...

    def close(self) -> None: ...


class Transport(Protocol):
    def open(self, request: TransportRequest) -> TransportResponse:
        """Send one GET and return as soon as the headers are in. Raises ``TransportError``."""
        ...


def header_values(headers: Sequence[tuple[str, str]], name: str) -> list[str]:
    wanted = name.lower()
    return [v for k, v in headers if k.lower() == wanted]


def header_value(headers: Sequence[tuple[str, str]], name: str) -> str:
    values = header_values(headers, name)
    return values[0].strip() if values else ""


class _StdlibResponse:
    def __init__(
        self, conn: http.client.HTTPConnection, response: http.client.HTTPResponse
    ) -> None:
        self._conn = conn
        self._response = response
        self.status: int = response.status
        self.headers: Sequence[tuple[str, str]] = tuple(response.getheaders())

    def iter_body(self, chunk_size: int = 16384) -> Iterator[bytes]:
        try:
            while True:
                chunk = self._response.read(chunk_size)
                if not chunk:
                    return
                yield chunk
        except TimeoutError:
            raise TransportError("read timeout", timeout=True) from None
        except (OSError, http.client.HTTPException):
            raise TransportError("read failed") from None

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._conn.close()


class StdlibTransport:
    """``http.client`` over a socket connected to a validated address."""

    def open(self, request: TransportRequest) -> TransportResponse:
        sock = self._connect(request)
        conn = http.client.HTTPConnection(request.host, request.port)
        conn.sock = sock
        try:
            conn.putrequest("GET", request.path, skip_host=True, skip_accept_encoding=True)
            for name, value in request.headers.items():
                conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
        except TimeoutError:
            conn.close()
            raise TransportError("timeout", timeout=True) from None
        except (OSError, http.client.HTTPException, ValueError):
            conn.close()
            raise TransportError("request failed") from None
        return _StdlibResponse(conn, response)

    def _connect(self, request: TransportRequest) -> socket.socket:
        last_timeout = False
        for address in request.addresses:
            try:
                sock = socket.create_connection(
                    (address, request.port), timeout=request.connect_timeout
                )
            except TimeoutError:
                last_timeout = True
                continue
            except OSError:
                continue
            try:
                if request.scheme == "https":
                    context = ssl.create_default_context()  # verifies chain and host name
                    sock = context.wrap_socket(sock, server_hostname=request.host)
                sock.settimeout(request.read_timeout)
            except TimeoutError:
                sock.close()
                last_timeout = True
                continue
            except (OSError, ssl.SSLError):
                sock.close()
                continue
            return sock
        raise TransportError("connect failed", timeout=last_timeout)
