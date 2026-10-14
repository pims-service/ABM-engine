"""Fakes for the website fetcher tests (issue #94): no network, no waiting, no DNS."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.integrations.fetch import PublicWebFetcher
from apps.integrations.fetch.transport import TransportError, TransportRequest

PUBLIC_IP = "93.184.216.34"
NOW = datetime(2026, 10, 13, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real network use (connect, DNS) fails the test."""

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


class FakeClock:
    """A monotonic clock that only moves when ``sleep`` or ``advance`` is called."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeResolver:
    """``answers`` maps a host to a list of IPs, or to a list of lists (one answer per call:
    the last one repeats), which simulates DNS rebinding."""

    def __init__(self, answers: dict[str, Any] | None = None, default: Any = None) -> None:
        self.answers = answers or {}
        self.default = [PUBLIC_IP] if default is None else default
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        answer = self.answers.get(host, self.default)
        if isinstance(answer, Exception):
            raise answer
        if answer and isinstance(answer[0], list):
            index = min(sum(1 for h, _ in self.calls if h == host) - 1, len(answer) - 1)
            return list(answer[index])
        return list(answer)


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        headers: Sequence[tuple[str, str]] = (),
        *,
        chunks: Iterator[bytes] | None = None,
        content_type: str | None = "text/html; charset=utf-8",
    ) -> None:
        self.status = status
        hdrs = list(headers)
        if content_type is not None and not any(k.lower() == "content-type" for k, _ in hdrs):
            hdrs.append(("Content-Type", content_type))
        self.headers = tuple(hdrs)
        self._body = body
        self._chunks = chunks
        self.closed = False
        self.chunks_read = 0

    def iter_body(self, chunk_size: int = 16384) -> Iterator[bytes]:
        if self._chunks is not None:
            for chunk in self._chunks:
                self.chunks_read += 1
                yield chunk
            return
        for i in range(0, len(self._body), chunk_size):
            self.chunks_read += 1
            yield self._body[i : i + chunk_size]

    def close(self) -> None:
        self.closed = True


def redirect(location: str, status: int = 302) -> FakeResponse:
    return FakeResponse(status, headers=[("Location", location)], content_type=None)


class FakeTransport:
    """Routes ``"host/path"`` to a ``FakeResponse``, an exception to raise, or a callable taking
    the ``TransportRequest``. Unknown routes answer 404. Every request is recorded."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = routes or {}
        self.requests: list[TransportRequest] = []

    def open(self, request: TransportRequest) -> FakeResponse:
        self.requests.append(request)
        handler = self.routes.get(f"{request.host}{request.path}")
        if handler is None:
            return FakeResponse(404, b"not found", content_type="text/plain")
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            result = handler(request)
            if isinstance(result, BaseException):
                raise result
            return result  # type: ignore[no-any-return]
        return handler  # type: ignore[no-any-return]

    def urls(self) -> list[str]:
        return [f"{r.scheme}://{r.host}{r.path}" for r in self.requests]

    def hosts_contacted(self) -> set[str]:
        return {r.host for r in self.requests}


def timeout_error() -> TransportError:
    return TransportError("timeout", timeout=True)


def make_fetcher(
    routes: dict[str, Any] | None = None,
    *,
    resolver: FakeResolver | None = None,
    clock: FakeClock | None = None,
    **kwargs: Any,
) -> tuple[PublicWebFetcher, FakeTransport, FakeClock, FakeResolver]:
    transport = FakeTransport(routes)
    clock = clock or FakeClock()
    resolver = resolver or FakeResolver()
    fetcher = PublicWebFetcher(
        transport,
        resolver,
        clock=clock,
        sleep=clock.sleep,
        now=lambda: NOW,
        user_agent=kwargs.pop("user_agent", None),
        **kwargs,
    )
    return fetcher, transport, clock, resolver


def page(body: str = "<html><body><p>Hello world</p></body></html>") -> FakeResponse:
    return FakeResponse(200, body.encode("utf-8"))


def robots(text: str, status: int = 200) -> FakeResponse:
    return FakeResponse(status, text.encode("utf-8"), content_type="text/plain")


Handler = Callable[[TransportRequest], FakeResponse]
