"""Retry with exponential backoff and jitter (issue #78).

``RetryPolicy`` is data plus one pure decision function (``delay_for``); ``call_with_retry`` runs a
callable under it. Only a ``ProviderError`` whose ``retryable`` flag is set is retried (the typed
errors of ``apps.integrations.framework.errors`` decide, never a message). Everything that touches
time or randomness is injectable, so tests need neither sleeping nor luck:

    policy = RetryPolicy(max_attempts=4, base_delay=1.0, factor=2.0, max_delay=10.0, jitter=0.25)
    result = call_with_retry(lambda: adapter.search(req), policy, sleep=fake_sleep)

Delay before retry ``n`` (1 = first retry): ``min(max_delay, base_delay * factor ** (n - 1))``,
reduced by up to ``jitter`` of itself at random (never increased, so ``max_delay`` stays a cap).
A ``Retry-After`` from the provider (``error.retry_after``) wins over the computed delay and is
not jittered. A ``Retry-After`` longer than ``max_retry_after`` is *not* slept inline: the error is
raised so the caller (a job) can re-queue with that delay instead of holding a worker.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from apps.integrations.framework.errors import ProviderError

from . import config
from .errors import ProviderCircuitOpen

logger = logging.getLogger(__name__)

Sleep = Callable[[float], None]
Clock = Callable[[], float]
OnRetry = Callable[[int, ProviderError, float], None]


@dataclass(frozen=True)
class RetryPolicy:
    """How many times and how long to wait. ``max_attempts`` counts the first call (1 = never
    retry). All durations are seconds."""

    max_attempts: int = 3
    base_delay: float = 0.5
    factor: float = 2.0
    max_delay: float = 8.0
    jitter: float = 0.25
    max_retry_after: float = 15.0
    max_elapsed: float | None = None  # stop retrying once this much time has passed in total

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if self.base_delay < 0 or self.max_delay < 0 or self.max_retry_after < 0:
            raise ValueError("Delays must not be negative.")
        if self.factor < 1:
            raise ValueError("factor must be at least 1.")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1.")

    @classmethod
    def from_settings(cls) -> RetryPolicy:
        return cls(
            max_attempts=int(config.get("retry_max_attempts")),
            base_delay=float(config.get("retry_base_delay")),
            factor=float(config.get("retry_factor")),
            max_delay=float(config.get("retry_max_delay")),
            jitter=float(config.get("retry_jitter")),
            max_retry_after=float(config.get("retry_max_retry_after")),
        )

    @classmethod
    def no_retry(cls) -> RetryPolicy:
        """For operations that are not safe to repeat."""
        return cls(max_attempts=1)

    def is_retryable(self, error: BaseException) -> bool:
        """Only a typed provider error that says so; the open circuit is never retried inline."""
        return (
            isinstance(error, ProviderError)
            and error.retryable
            and not isinstance(error, ProviderCircuitOpen)
        )

    def backoff(self, retry_number: int, rng: Callable[[], float] = random.random) -> float:
        """The computed delay before retry ``retry_number`` (1 = the first retry), jittered."""
        raw = min(self.max_delay, self.base_delay * self.factor ** (retry_number - 1))
        return raw * (1.0 - self.jitter * rng())

    def delay_for(
        self,
        attempt: int,
        error: BaseException,
        rng: Callable[[], float] = random.random,
    ) -> float | None:
        """Seconds to wait before the next attempt after ``attempt`` (1-based) failed with
        ``error``, or ``None`` when it must not be retried (not retryable, out of attempts, or a
        Retry-After too long to sleep inline)."""
        if attempt >= self.max_attempts or not self.is_retryable(error):
            return None
        hinted = error.retry_after if isinstance(error, ProviderError) else None
        if hinted is not None:
            return hinted if hinted <= self.max_retry_after else None
        return self.backoff(attempt, rng)


def call_with_retry[T](
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
    rng: Callable[[], float] = random.random,
    on_retry: OnRetry | None = None,
) -> T:
    """Call ``fn()``; on a retryable ``ProviderError`` wait and call again (see the module doc).

    Re-raises the last error when it is not retryable or the attempts, ``max_elapsed`` or the
    inline wait limit are used up. Any other exception propagates immediately. ``on_retry(attempt,
    error, delay)`` is called before each sleep (for logging and metrics).
    """
    started = clock()
    attempt = 1
    while True:
        try:
            return fn()
        except ProviderError as error:
            delay = policy.delay_for(attempt, error, rng)
            if delay is None:
                raise
            if policy.max_elapsed is not None and (clock() - started) + delay > policy.max_elapsed:
                raise
            logger.info(
                "provider call failed, retrying",
                extra={
                    "code": error.code,
                    "provider": error.provider_name,
                    "attempt": attempt,
                    "delay_seconds": round(delay, 3),
                },
            )
            if on_retry is not None:
                on_retry(attempt, error, delay)
            sleep(delay)
            attempt += 1
