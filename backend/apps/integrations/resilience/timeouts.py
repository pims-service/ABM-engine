"""Timeouts as data, and a hard deadline for in-process calls (issue #78).

``TimeoutPolicy`` carries the connect and read timeouts an adapter hands to its HTTP client
(``httpx.Timeout(policy.read, connect=policy.connect)`` or ``requests`` ``policy.as_tuple()``)
and the hard ``deadline`` for one whole call. Adapters may allow a per-operation override with
``policy.scaled(3)`` (verification and LLM calls are slower than lookups).

``run_with_deadline`` is the last line of defense against a call that never returns (a client
without a timeout, a vendor SDK stuck in a socket). It runs the call in a daemon thread and stops
*waiting* after ``deadline`` seconds, raising ``ProviderTimeout``. Limits, by design:

* Python cannot kill a thread. The abandoned call keeps running in the background until it
  returns, and its side effects (a request already sent, credits spent) may still happen.
* To bound the damage the number of abandoned threads per process is capped
  (``max_abandoned_calls``); beyond it new calls are refused with ``ProviderUnavailable`` until
  hung ones finish. The worker itself is never blocked longer than ``deadline``.
* The call runs in another thread, so it must not use the Django ORM (database connections are
  per thread). Adapters are pure (credentials in, HTTP out), so this holds for them.
* The context (logging ids) is copied into the thread.

Real protection is still a timeout in the HTTP client; this wrapper catches what slips through.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, wait
from dataclasses import dataclass

from apps.integrations.framework.errors import ProviderTimeout, ProviderUnavailable

from . import config

logger = logging.getLogger(__name__)

_abandoned = 0
_abandoned_lock = threading.Lock()


@dataclass(frozen=True)
class TimeoutPolicy:
    """Seconds. ``deadline`` of ``None`` or 0 disables the in-process wrapper."""

    connect: float = 3.05
    read: float = 30.0
    deadline: float | None = 120.0

    def __post_init__(self) -> None:
        if self.connect <= 0 or self.read <= 0:
            raise ValueError("Timeouts must be positive.")
        if self.deadline is not None and self.deadline < 0:
            raise ValueError("deadline must not be negative.")

    @classmethod
    def from_settings(cls) -> TimeoutPolicy:
        deadline = float(config.get("hard_deadline"))
        return cls(
            connect=float(config.get("connect_timeout")),
            read=float(config.get("read_timeout")),
            deadline=deadline or None,
        )

    def scaled(self, factor: float) -> TimeoutPolicy:
        """A copy with every duration multiplied (a per-operation override)."""
        return TimeoutPolicy(
            self.connect * factor,
            self.read * factor,
            self.deadline * factor if self.deadline else self.deadline,
        )

    def as_tuple(self) -> tuple[float, float]:
        """``(connect, read)`` as ``requests`` expects."""
        return (self.connect, self.read)

    def as_dict(self) -> dict[str, float | None]:
        return {"connect": self.connect, "read": self.read, "deadline": self.deadline}


def abandoned_calls() -> int:
    """How many timed-out calls are still running in this process (a health signal)."""
    return _abandoned


def run_with_deadline[T](fn: Callable[[], T], deadline: float | None) -> T:
    """Run ``fn()`` and return its result, or raise ``ProviderTimeout`` after ``deadline`` seconds.

    An exception raised by ``fn`` propagates unchanged. ``deadline`` of ``None`` or 0 just calls
    ``fn`` in the current thread. See the module doc for the limits.
    """
    global _abandoned
    if not deadline:
        return fn()
    cap = int(config.get("max_abandoned_calls"))
    with _abandoned_lock:
        if _abandoned >= cap:
            logger.error("too many hung provider calls", extra={"abandoned": _abandoned})
            raise ProviderUnavailable()

    future: Future[T] = Future()
    ctx = contextvars.copy_context()
    state = {"abandoned": False}

    def target() -> None:
        global _abandoned
        try:
            future.set_result(ctx.run(fn))
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            with _abandoned_lock:
                if state["abandoned"]:
                    _abandoned -= 1

    thread = threading.Thread(target=target, name="provider-call", daemon=True)
    thread.start()
    wait([future], timeout=deadline)
    with _abandoned_lock:
        if not future.done():
            state["abandoned"] = True
            _abandoned += 1
            hung = True
        else:
            hung = False
    if hung:
        logger.warning("provider call exceeded its deadline", extra={"deadline": deadline})
        raise ProviderTimeout()
    return future.result()
