"""Per-host politeness for the website fetcher (issue #94).

``HostRateLimiter`` keeps at least ``delay`` seconds between two requests to the same host. It is
in-process on purpose: the resilience ``RateLimiter`` budgets a vendor API key across workers in
the shared cache, while here we only need to avoid hammering one site from one process, and a
cache round trip per page would cost more than it protects. The clock and the sleep function are
injectable, so tests never wait.

The limiter is thread safe: a caller reserves its slot under a lock and sleeps outside it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

MAX_HOSTS = 2000


class WaitTooLong(Exception):
    """The wait for the next slot would exceed what the caller can afford."""


class HostRateLimiter:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def acquire(self, host: str, delay: float, *, max_wait: float | None = None) -> float:
        """Wait until ``host`` may be contacted; returns the seconds waited. Raises
        ``WaitTooLong`` (and reserves nothing) if the wait would exceed ``max_wait``."""
        delay = max(0.0, delay)
        with self._lock:
            now = self.clock()
            start = max(now, self._next.get(host, now))
            wait = start - now
            if max_wait is not None and wait > max_wait:
                raise WaitTooLong
            if len(self._next) >= MAX_HOSTS and host not in self._next:
                for stale in [h for h, t in self._next.items() if t <= now][: MAX_HOSTS // 2]:
                    del self._next[stale]
            self._next[host] = start + delay
        if wait > 0:
            self._sleep(wait)
        return wait


_shared: HostRateLimiter | None = None
_shared_lock = threading.Lock()


def shared_limiter() -> HostRateLimiter:
    """The process-wide limiter every default ``PublicWebFetcher`` uses."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = HostRateLimiter()
        return _shared
