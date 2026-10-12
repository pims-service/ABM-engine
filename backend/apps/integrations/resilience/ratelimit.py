"""A token-bucket rate limiter shared by every worker process (issue #78, ADR 0014).

One limiter guards one ``(client, adapter)`` pair, because a vendor limit applies to the API key
and the key belongs to a client's connection. It enforces ``AdapterCapabilities.rate_limit``
(``per_second``, ``per_minute``, ``per_day``) with one bucket per declared window: capacity =
the limit, refilled continuously at ``limit / window`` tokens per second. A call needs a token in
every bucket (so a burst never exceeds the per second limit, and the minute and day budgets are
spent as calls go out). Tokens are taken only when all buckets have one.

State lives in the shared cache (``store.SharedState``), so two worker processes asking at the same
moment see one budget. Time is the wall clock (``time.time``), the only clock processes share;
a backwards jump is treated as no time passing.

* ``try_acquire()`` never waits: it returns a ``RateDecision`` (``allowed``, ``retry_after``).
* ``acquire(timeout)`` waits for a token, but never longer than ``min(timeout, max_block)``
  (``rate_limit_max_block`` setting): beyond that it raises ``ProviderRateLimited`` carrying the
  wait in ``retry_after``, and the caller re-queues instead of holding a worker.

Only ids and counts are stored; no secrets.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apps.integrations.framework.base import Adapter, RateLimit
from apps.integrations.framework.errors import ProviderRateLimited

from . import config
from .store import SharedState

logger = logging.getLogger(__name__)

WINDOWS: tuple[tuple[str, int], ...] = (("per_second", 1), ("per_minute", 60), ("per_day", 86_400))


@dataclass(frozen=True)
class RateDecision:
    """The answer of ``try_acquire``: ``bool(decision)`` is ``allowed``."""

    allowed: bool
    retry_after: float = 0.0  # seconds until a token is available (0 when allowed)

    def __bool__(self) -> bool:
        return self.allowed


def limits_for(adapter_key: str, declared: RateLimit | None) -> RateLimit | None:
    """The effective limits: the ``rate_limits`` override in settings for this adapter key (it
    may only be stricter or set a missing window) on top of what the adapter declares."""
    override = config.get("rate_limits").get(adapter_key) or {}
    values = {name: getattr(declared, name, None) for name, _ in WINDOWS}
    for name, _ in WINDOWS:
        wanted = override.get(name)
        if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted > 0:
            current = values[name]
            values[name] = wanted if current is None else min(current, wanted)
    if all(v is None for v in values.values()):
        return None
    return RateLimit(**values)


class RateLimiter:
    """See the module doc. ``limit`` of ``None`` (or all windows ``None``) never limits."""

    def __init__(
        self,
        scope: str,
        limit: RateLimit | None,
        *,
        state: SharedState | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        max_block: float | None = None,
        provider_name: str = "",
    ) -> None:
        self.scope = scope
        self.limit = limit
        self.state = state or SharedState()
        self._clock = clock
        self._sleep = sleep
        self.max_block = (
            float(config.get("rate_limit_max_block")) if max_block is None else max_block
        )
        self.provider_name = provider_name
        for name, _ in WINDOWS:
            value = getattr(limit, name, None)
            if value is not None and (not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer.")

    @classmethod
    def for_adapter(cls, adapter: Adapter, client_id: object, **kwargs: Any) -> RateLimiter:
        """The limiter for ``adapter`` as used by ``client_id`` (limits from its capabilities,
        stricter settings overrides applied)."""
        limit = limits_for(adapter.key, adapter.capabilities.rate_limit)
        scope = f"{client_id}:{adapter.category.value}:{adapter.key}"
        return cls(scope, limit, provider_name=adapter.key, **kwargs)

    # ------------------------------------------------------------------ helpers
    def _windows(self) -> list[tuple[str, int, int]]:
        """``(name, window seconds, capacity)`` of each configured window."""
        return [
            (name, seconds, getattr(self.limit, name))
            for name, seconds in WINDOWS
            if getattr(self.limit, name, None) is not None
        ]

    @property
    def enabled(self) -> bool:
        return bool(self._windows())

    def _refill(self, buckets: dict[str, Any], now: float) -> dict[str, list[float]]:
        """Current ``name -> [tokens, last_update]`` after refilling to ``now``."""
        out: dict[str, list[float]] = {}
        for name, seconds, capacity in self._windows():
            tokens, stamp = buckets.get(name, [float(capacity), now])
            elapsed = max(0.0, now - float(stamp))
            tokens = min(float(capacity), float(tokens) + elapsed * capacity / seconds)
            out[name] = [tokens, now]
        return out

    # ------------------------------------------------------------------ API
    def try_acquire(self, cost: int = 1) -> RateDecision:
        """Take ``cost`` tokens now if every window has them; never waits."""
        if cost < 1:
            raise ValueError("cost must be at least 1.")
        windows = self._windows()
        if not windows:
            return RateDecision(True)
        if any(cost > capacity for _, _, capacity in windows):
            raise ValueError("cost exceeds a rate limit window; it could never be allowed.")
        now = self._clock()
        with self.state.update(f"rl:{self.scope}", timeout=2 * max(s for _, s, _ in windows)) as s:
            buckets = self._refill(s.get("b", {}), now)
            wait = 0.0
            for name, seconds, capacity in windows:
                missing = cost - buckets[name][0]
                if missing > 1e-9:
                    wait = max(wait, missing * seconds / capacity)
            if wait == 0.0:
                for bucket in buckets.values():
                    bucket[0] -= cost
            s["b"] = buckets
        if wait:
            return RateDecision(False, math.ceil(wait * 1000) / 1000)
        return RateDecision(True)

    def acquire(self, timeout: float | None = None, cost: int = 1) -> None:
        """Wait for ``cost`` tokens for at most ``min(timeout, max_block)`` seconds.

        Raises ``ProviderRateLimited`` (``retry_after`` = how long until a token is free) when
        that is not enough. It sleeps only when the wait fits, so a worker is never held longer
        than the cap.
        """
        cap = self.max_block if timeout is None else min(timeout, self.max_block)
        started = self._clock()
        while True:
            decision = self.try_acquire(cost)
            if decision.allowed:
                return
            remaining = cap - (self._clock() - started)
            if decision.retry_after > remaining:
                logger.info(
                    "rate limit reached",
                    extra={"scope": self.scope, "retry_after": decision.retry_after},
                )
                raise ProviderRateLimited(decision.retry_after, provider_name=self.provider_name)
            self._sleep(decision.retry_after)

    def snapshot(self) -> dict[str, Any]:
        """Read-only view for health pages: per window the limit and the tokens left now."""
        now = self._clock()
        stored = (self.state.get(f"rl:{self.scope}") or {}).get("b", {})
        buckets = self._refill(stored, now)
        return {
            "enabled": self.enabled,
            "windows": [
                {"window": name, "limit": capacity, "remaining": int(buckets[name][0])}
                for name, _, capacity in self._windows()
            ],
        }

    def reset(self) -> None:
        self.state.delete(f"rl:{self.scope}")
