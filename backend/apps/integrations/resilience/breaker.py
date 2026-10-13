"""A circuit breaker shared by every worker process (issue #78, ADR 0014).

One breaker guards one ``(client, adapter)`` pair and pauses calls to a provider that keeps
failing, so a bulk run does not hammer (and wait on) a vendor that is down.

    closed ──N counted failures within `window`──▶ open ──`cooldown` passed──▶ half-open
       ▲                                            ▲                              │
       └───────────── probe call succeeds ──────────┼──────────────────────────────┤
                                                    └──── probe call fails ────────┘

* closed: calls pass; consecutive counted failures are remembered (a success forgets them).
* open: ``allow()`` raises ``ProviderCircuitOpen`` (a ``ProviderUnavailable``) with
  ``retry_after`` = the remaining cool-down.
* half-open: exactly one probe call is let through at a time (a lease that expires after the
  cool-down, so a worker that died mid-probe cannot wedge the circuit); the rest are refused.
  A good answer closes the circuit, a counted failure re-opens it for a new cool-down.

What counts as a failure (``counts_as_failure``): a *retryable* ``ProviderError`` (unavailable,
timeout, and rate limited only if ``breaker_count_rate_limited`` is on). Auth, bad request, not
found, quota and every other non-retryable error mean the provider answered: they never open the
circuit, and a half-open probe that gets one closes it.

State is in the shared cache (``store.SharedState``), time is the wall clock (injectable).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from apps.integrations.framework.base import Adapter
from apps.integrations.framework.errors import ProviderError, ProviderRateLimited

from . import config
from .errors import ProviderCircuitOpen
from .store import SharedState

logger = logging.getLogger(__name__)

CLOSED: Final = "closed"
OPEN: Final = "open"
HALF_OPEN: Final = "half_open"


@dataclass(frozen=True)
class BreakerPolicy:
    failure_threshold: int = 5
    window: float = 60.0
    cooldown: float = 30.0
    count_rate_limited: bool = False

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1.")
        if self.window <= 0 or self.cooldown <= 0:
            raise ValueError("window and cooldown must be positive.")

    @classmethod
    def from_settings(cls) -> BreakerPolicy:
        return cls(
            failure_threshold=int(config.get("breaker_failure_threshold")),
            window=float(config.get("breaker_window")),
            cooldown=float(config.get("breaker_cooldown")),
            count_rate_limited=bool(config.get("breaker_count_rate_limited")),
        )


class CircuitBreaker:
    def __init__(
        self,
        scope: str,
        policy: BreakerPolicy | None = None,
        *,
        state: SharedState | None = None,
        clock: Callable[[], float] = time.time,
        provider_name: str = "",
    ) -> None:
        self.scope = scope
        self.policy = policy or BreakerPolicy.from_settings()
        self.state = state or SharedState()
        self._clock = clock
        self.provider_name = provider_name

    @classmethod
    def for_adapter(cls, adapter: Adapter, client_id: object, **kwargs: Any) -> CircuitBreaker:
        scope = f"{client_id}:{adapter.category.value}:{adapter.key}"
        return cls(scope, provider_name=adapter.key, **kwargs)

    # ------------------------------------------------------------------ helpers
    @property
    def _name(self) -> str:
        return f"cb:{self.scope}"

    @property
    def _ttl(self) -> float:
        return 4 * max(self.policy.window, self.policy.cooldown) + 60

    def counts_as_failure(self, error: BaseException) -> bool:
        """Only outages count: retryable provider errors (see the module doc)."""
        if not isinstance(error, ProviderError) or not error.retryable:
            return False
        if isinstance(error, ProviderRateLimited):
            return self.policy.count_rate_limited
        return not isinstance(error, ProviderCircuitOpen)

    def _open(self, s: dict[str, Any], now: float) -> None:
        s.update(state=OPEN, opened_at=now, failures=[], probe_until=0.0)
        logger.warning("circuit opened", extra={"scope": self.scope})

    def _refused(self, retry_after: float) -> ProviderCircuitOpen:
        return ProviderCircuitOpen(
            retry_after=max(0.001, round(retry_after, 3)), provider_name=self.provider_name
        )

    # ------------------------------------------------------------------ API
    def current_state(self) -> str:
        """closed, open or half_open, without changing anything (a cooled-down open circuit
        reads as half_open: the next call becomes the probe)."""
        s = self.state.get(self._name) or {}
        if s.get("state", CLOSED) == OPEN and self._clock() - s.get("opened_at", 0) >= (
            self.policy.cooldown
        ):
            return HALF_OPEN
        return str(s.get("state", CLOSED))

    def allow(self) -> None:
        """Called before each attempt. Returns when the call may go ahead; raises
        ``ProviderCircuitOpen`` when it must not. In half-open state the caller that gets through
        is the probe and must report with ``record_success`` or ``record_failure``."""
        now = self._clock()
        with self.state.update(self._name, self._ttl) as s:
            current = s.get("state", CLOSED)
            if current == CLOSED:
                return
            if current == OPEN:
                remaining = s.get("opened_at", 0.0) + self.policy.cooldown - now
                if remaining > 0:
                    raise self._refused(remaining)
                s["state"] = HALF_OPEN
            # half-open: one probe at a time, the lease frees itself if the probe never reports
            lease = s.get("probe_until", 0.0)
            if lease > now:
                raise self._refused(lease - now)
            s["probe_until"] = now + self.policy.cooldown
            logger.info("circuit half-open: probing", extra={"scope": self.scope})

    def release_probe(self) -> None:
        """Give back a half-open probe slot that was granted but not used (the call was refused
        by something else, such as the rate limiter), so the next caller can probe."""
        with self.state.update(self._name, self._ttl) as s:
            if s.get("state") == HALF_OPEN:
                s["probe_until"] = 0.0

    def record_success(self) -> None:
        """The provider answered: forget failures; a half-open circuit closes."""
        with self.state.update(self._name, self._ttl) as s:
            if s.get("state", CLOSED) != CLOSED:
                logger.info("circuit closed", extra={"scope": self.scope})
            s.clear()

    def record_failure(self, error: BaseException) -> None:
        """Report a failed call. Errors that are not outages (see ``counts_as_failure``) count
        as an answer from the provider and are treated like a success."""
        if not self.counts_as_failure(error):
            self.record_success()
            return
        now = self._clock()
        with self.state.update(self._name, self._ttl) as s:
            current = s.get("state", CLOSED)
            if current == OPEN:
                return  # a call that started before the circuit opened: already accounted for
            if current == HALF_OPEN:
                self._open(s, now)  # the probe failed: a new cool-down
                return
            recent = [t for t in s.get("failures", []) if now - t < self.policy.window]
            recent.append(now)
            s["state"] = CLOSED
            s["failures"] = recent[-self.policy.failure_threshold :]
            if len(recent) >= self.policy.failure_threshold:
                self._open(s, now)

    def snapshot(self) -> dict[str, Any]:
        """Read-only view for health pages (no secrets, no payloads)."""
        s = self.state.get(self._name) or {}
        now = self._clock()
        state = self.current_state()
        retry_after = 0.0
        if state == OPEN:
            retry_after = max(0.0, s.get("opened_at", 0.0) + self.policy.cooldown - now)
        return {
            "state": state,
            "recent_failures": len(
                [t for t in s.get("failures", []) if now - t < self.policy.window]
            ),
            "failure_threshold": self.policy.failure_threshold,
            "retry_after": round(retry_after, 3),
        }

    def reset(self) -> None:
        self.state.delete(self._name)
