"""The one call adapters' callers use: ``resilient_execute`` (issue #78, ADR 0014).

It wraps the framework's ``execute(adapter, operation, *args)`` (which enforces the adapter
contract and the typed errors) with, per attempt:

1. the circuit breaker (``ProviderCircuitOpen`` while the provider is paused),
2. the rate limiter (bounded wait, then ``ProviderRateLimited`` with ``retry_after``),
3. the call itself under the hard deadline (``ProviderTimeout`` for a hung call),
4. the outcome reported to the breaker;

and around all attempts the retry policy (backoff, jitter, ``Retry-After``, only ``retryable``
errors). Whatever escapes is a ``ProviderError`` (code, ``retryable``, ``retry_after``), which is
what the job layer (``apps.integrations.tasks``) turns into a re-queue or a failed item.

    result = resilient_execute(adapter, adapter.search, request, client_id=client.pk)

``client_id`` builds the shared limiter and breaker for ``(client, adapter)`` from the adapter's
capabilities and the settings. Pass ``limiter=``, ``breaker=``, ``policy=`` or ``timeouts=`` to
override any of them (``None`` limiter and breaker with no ``client_id`` means "do not use").
Use ``policy=RetryPolicy.no_retry()`` for operations that are not safe to repeat. The names
``policy``, ``limiter``, ``breaker``, ``timeouts``, ``client_id``, ``sleep``, ``clock`` and ``rng``
are reserved: they are not passed on to the operation.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apps.integrations.framework.base import Adapter
from apps.integrations.framework.envelope import AdapterResult
from apps.integrations.framework.errors import ProviderError
from apps.integrations.framework.operations import execute

from . import config
from .breaker import BreakerPolicy, CircuitBreaker
from .ratelimit import RateLimiter
from .retry import RetryPolicy, call_with_retry
from .timeouts import TimeoutPolicy, run_with_deadline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resilience:
    """The four pieces for one ``(client, adapter)``; build once per task and reuse."""

    policy: RetryPolicy
    timeouts: TimeoutPolicy
    limiter: RateLimiter | None = None
    breaker: CircuitBreaker | None = None

    @classmethod
    def for_adapter(
        cls,
        adapter: Adapter,
        client_id: object,
        *,
        policy: RetryPolicy | None = None,
        timeouts: TimeoutPolicy | None = None,
        breaker_policy: BreakerPolicy | None = None,
    ) -> Resilience:
        return cls(
            policy=policy or RetryPolicy.from_settings(),
            timeouts=timeouts or TimeoutPolicy.from_settings(),
            limiter=RateLimiter.for_adapter(adapter, client_id),
            breaker=(
                CircuitBreaker.for_adapter(adapter, client_id, policy=breaker_policy)
                if config.get("breaker_enabled")
                else None
            ),
        )


def resilient_execute(
    adapter: Adapter,
    operation: Callable[..., AdapterResult[Any]],
    /,
    *args: Any,
    **kwargs: Any,
) -> AdapterResult[Any]:
    """Run ``operation(*args, **kwargs)`` through ``execute`` with retries, rate limiting, the
    circuit breaker and the hard deadline (see the module doc for the reserved keyword names)."""
    policy: RetryPolicy | None = kwargs.pop("policy", None)
    limiter: RateLimiter | None = kwargs.pop("limiter", None)
    breaker: CircuitBreaker | None = kwargs.pop("breaker", None)
    timeouts: TimeoutPolicy | None = kwargs.pop("timeouts", None)
    client_id: object = kwargs.pop("client_id", None)
    sleep: Callable[[float], None] = kwargs.pop("sleep", time.sleep)
    clock: Callable[[], float] = kwargs.pop("clock", time.monotonic)
    rng: Callable[[], float] = kwargs.pop("rng", random.random)

    if client_id is not None:
        defaults = Resilience.for_adapter(adapter, client_id, policy=policy, timeouts=timeouts)
        policy = defaults.policy
        timeouts = defaults.timeouts
        limiter = limiter or defaults.limiter
        breaker = breaker or defaults.breaker
    policy = policy or RetryPolicy.from_settings()
    deadline = (timeouts or TimeoutPolicy.from_settings()).deadline
    op_name = getattr(operation, "__name__", "operation")

    def attempt() -> AdapterResult[Any]:
        if breaker is not None:
            breaker.allow()
        if limiter is not None:
            try:
                limiter.acquire()
            except ProviderError:
                if breaker is not None:
                    breaker.release_probe()
                raise
        try:
            result = run_with_deadline(
                lambda: execute(adapter, operation, *args, **kwargs), deadline
            )
        except ProviderError as error:
            if breaker is not None:
                breaker.record_failure(error)
            raise
        if breaker is not None:
            breaker.record_success()
        return result

    try:
        return call_with_retry(attempt, policy, sleep=sleep, clock=clock, rng=rng)
    except ProviderError as error:
        logger.info(
            "provider operation failed",
            extra={
                "provider": adapter.key,
                "category": adapter.category.value,
                "operation": op_name,
                "code": error.code,
                "retryable": error.retryable,
                "retry_after": error.retry_after,
            },
        )
        raise
