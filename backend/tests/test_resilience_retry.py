"""Retry policy, backoff, jitter, Retry-After precedence and the hard deadline (issue #78).

Pure unit tests: a fake clock and a fake sleep, so nothing waits and nothing is random.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable

import pytest

from apps.integrations.framework import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderNotFound,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUpstreamError,
)
from apps.integrations.resilience import (
    ProviderCircuitOpen,
    RetryPolicy,
    TimeoutPolicy,
    call_with_retry,
    run_with_deadline,
)
from apps.integrations.resilience import timeouts as timeouts_module
from tests.resilience_helpers import FakeClock

NO_JITTER = RetryPolicy(base_delay=1.0, factor=2.0, max_delay=10.0, jitter=0.0, max_attempts=5)


def flaky(*errors: ProviderError, then: str = "ok") -> Callable[[], str]:
    """A callable that raises the given errors in turn, then returns ``then``."""
    queue = list(errors)
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if queue:
            raise queue.pop(0)
        return then

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


# ------------------------------------------------------------------ which errors retry


@pytest.mark.parametrize(
    ("error", "retried"),
    [
        (ProviderUnavailable(), True),
        (ProviderTimeout(), True),
        (ProviderRateLimited(2), True),
        (ProviderRateLimited(), True),
        (ProviderAuthError(), False),
        (ProviderBadRequest("name"), False),
        (ProviderNotFound(), False),
        (ProviderQuotaExceeded(), False),
        (ProviderUpstreamError(), False),
        (ProviderCircuitOpen(retry_after=5), False),  # a job re-queues it, nothing sleeps on it
    ],
)
def test_only_retryable_typed_errors_are_retried(error: ProviderError, retried: bool) -> None:
    clock = FakeClock()
    fn = flaky(error)
    if retried:
        assert call_with_retry(fn, NO_JITTER, sleep=clock.sleep) == "ok"
        assert fn.calls["n"] == 2  # type: ignore[attr-defined]
        assert len(clock.sleeps) == 1
    else:
        with pytest.raises(type(error)):
            call_with_retry(fn, NO_JITTER, sleep=clock.sleep)
        assert fn.calls["n"] == 1  # type: ignore[attr-defined]
        assert clock.sleeps == []


def test_untyped_exceptions_propagate_without_retry() -> None:
    clock = FakeClock()
    calls = []

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        call_with_retry(boom, NO_JITTER, sleep=clock.sleep)
    assert calls == [1]


# ------------------------------------------------------------------ attempts and backoff


def test_max_attempts_counts_the_first_call_and_the_last_error_is_raised() -> None:
    clock = FakeClock()
    errors = [ProviderUnavailable(provider_name=f"p{i}") for i in range(10)]
    fn = flaky(*errors)
    policy = RetryPolicy(max_attempts=3, jitter=0.0, base_delay=1.0)
    with pytest.raises(ProviderUnavailable) as caught:
        call_with_retry(fn, policy, sleep=clock.sleep)
    assert fn.calls["n"] == 3  # type: ignore[attr-defined]
    assert caught.value is errors[2]
    assert clock.sleeps == [1.0, 2.0]


def test_one_attempt_means_no_retry() -> None:
    fn = flaky(ProviderUnavailable())
    with pytest.raises(ProviderUnavailable):
        call_with_retry(fn, RetryPolicy.no_retry(), sleep=FakeClock().sleep)
    assert fn.calls["n"] == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("retry", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 10.0), (9, 10.0)],  # capped at max_delay
)
def test_exponential_backoff_table(retry: int, expected: float) -> None:
    assert NO_JITTER.backoff(retry) == expected


def test_backoff_is_deterministic_without_jitter_and_sleeps_in_order() -> None:
    clock = FakeClock()
    fn = flaky(*[ProviderTimeout() for _ in range(4)])
    assert call_with_retry(fn, NO_JITTER, sleep=clock.sleep) == "ok"
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]


# ------------------------------------------------------------------ jitter


def test_jitter_only_shortens_and_stays_within_bounds_with_a_seeded_rng() -> None:
    policy = RetryPolicy(base_delay=4.0, factor=2.0, max_delay=100.0, jitter=0.25)
    rng = random.Random(1234)
    for retry in range(1, 6):
        raw = min(100.0, 4.0 * 2.0 ** (retry - 1))
        for _ in range(200):
            delay = policy.backoff(retry, rng.random)
            assert raw * 0.75 <= delay <= raw


def test_jitter_extremes_use_the_injected_random() -> None:
    policy = RetryPolicy(base_delay=8.0, jitter=0.5)
    assert policy.backoff(1, lambda: 0.0) == 8.0  # no reduction
    assert policy.backoff(1, lambda: 1.0) == 4.0  # full reduction


def test_same_seed_gives_the_same_delays() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=1.0, jitter=0.5)

    def run(seed: int) -> list[float]:
        clock = FakeClock()
        call_with_retry(
            flaky(*[ProviderUnavailable() for _ in range(5)]),
            policy,
            sleep=clock.sleep,
            rng=random.Random(seed).random,
        )
        return clock.sleeps

    assert run(7) == run(7)
    assert run(7) != run(8)


# ------------------------------------------------------------------ Retry-After


def test_retry_after_wins_over_the_computed_backoff() -> None:
    clock = FakeClock()
    fn = flaky(ProviderRateLimited(7.5))
    assert call_with_retry(fn, NO_JITTER, sleep=clock.sleep) == "ok"
    assert clock.sleeps == [7.5]  # not the 1.0 computed backoff, and not jittered


def test_retry_after_smaller_than_the_backoff_is_still_honoured() -> None:
    clock = FakeClock()
    call_with_retry(flaky(ProviderRateLimited(0.2)), NO_JITTER, sleep=clock.sleep)
    assert clock.sleeps == [0.2]


def test_retry_after_without_a_value_falls_back_to_the_backoff() -> None:
    clock = FakeClock()
    call_with_retry(flaky(ProviderRateLimited(None)), NO_JITTER, sleep=clock.sleep)
    assert clock.sleeps == [1.0]


def test_retry_after_longer_than_the_inline_limit_is_raised_not_slept() -> None:
    clock = FakeClock()
    policy = RetryPolicy(jitter=0.0, max_retry_after=10.0)
    with pytest.raises(ProviderRateLimited) as caught:
        call_with_retry(flaky(ProviderRateLimited(300)), policy, sleep=clock.sleep)
    assert caught.value.retry_after == 300  # the caller (a job) can re-queue with it
    assert clock.sleeps == []


def test_retry_after_on_unavailable_is_honoured_too() -> None:
    clock = FakeClock()
    call_with_retry(flaky(ProviderUnavailable(retry_after=3)), NO_JITTER, sleep=clock.sleep)
    assert clock.sleeps == [3.0]


def test_max_elapsed_stops_retrying() -> None:
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=10, base_delay=5.0, jitter=0.0, max_delay=5.0, max_elapsed=12)
    fn = flaky(*[ProviderUnavailable() for _ in range(10)])
    with pytest.raises(ProviderUnavailable):
        call_with_retry(fn, policy, sleep=clock.sleep, clock=clock)
    assert clock.sleeps == [5.0, 5.0]  # a third wait would pass 12 seconds


def test_on_retry_sees_attempt_error_and_delay() -> None:
    seen: list[tuple[int, str, float]] = []
    call_with_retry(
        flaky(ProviderUnavailable(), ProviderRateLimited(2)),
        NO_JITTER,
        sleep=FakeClock().sleep,
        on_retry=lambda attempt, error, delay: seen.append((attempt, error.code, delay)),
    )
    assert seen == [(1, "provider_unavailable", 1.0), (2, "provider_rate_limited", 2.0)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay": -1},
        {"factor": 0.5},
        {"jitter": 1.5},
        {"max_retry_after": -1},
    ],
)
def test_policy_rejects_nonsense(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match=r"."):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_policy_from_settings_uses_overrides(settings: object) -> None:
    settings.INTEGRATION_RESILIENCE = {"retry_max_attempts": 7, "retry_jitter": 0.0}  # type: ignore[attr-defined]
    policy = RetryPolicy.from_settings()
    assert (policy.max_attempts, policy.jitter) == (7, 0.0)


# ------------------------------------------------------------------ timeouts and deadline


def test_timeout_policy_is_data_for_http_clients() -> None:
    policy = TimeoutPolicy(connect=2, read=20, deadline=60)
    assert policy.as_tuple() == (2, 20)
    assert policy.scaled(3).as_dict() == {"connect": 6, "read": 60, "deadline": 180}
    assert TimeoutPolicy(deadline=None).scaled(2).deadline is None
    with pytest.raises(ValueError, match="positive"):
        TimeoutPolicy(connect=0)


def test_deadline_returns_the_result_and_propagates_exceptions() -> None:
    assert run_with_deadline(lambda: 42, 5) == 42
    assert run_with_deadline(lambda: 42, None) == 42  # no wrapper, same thread
    with pytest.raises(ProviderBadRequest):
        run_with_deadline(_raise_bad_request, 5)
    with pytest.raises(TimeoutError):  # the callable's own TimeoutError is not our deadline
        run_with_deadline(_raise_timeout_error, 5)


def _raise_bad_request() -> None:
    raise ProviderBadRequest("name")


def _raise_timeout_error() -> None:
    raise TimeoutError("inner")


def test_a_hung_call_cannot_block_longer_than_the_deadline() -> None:
    release = threading.Event()
    before = timeouts_module.abandoned_calls()
    try:
        with pytest.raises(ProviderTimeout):
            run_with_deadline(lambda: release.wait(30), 0.05)
        assert timeouts_module.abandoned_calls() == before + 1
    finally:
        release.set()  # let the abandoned thread finish
    for _ in range(200):
        if timeouts_module.abandoned_calls() == before:
            break
        threading.Event().wait(0.01)
    assert timeouts_module.abandoned_calls() == before


def test_too_many_hung_calls_are_refused_instead_of_piling_up(settings: object) -> None:
    settings.INTEGRATION_RESILIENCE = {"max_abandoned_calls": 1}  # type: ignore[attr-defined]
    release = threading.Event()
    try:
        with pytest.raises(ProviderTimeout):
            run_with_deadline(lambda: release.wait(30), 0.05)
        started = threading.Event()
        with pytest.raises(ProviderUnavailable) as caught:
            run_with_deadline(started.set, 5)
        assert not started.is_set()  # never even started
        assert not isinstance(caught.value, ProviderTimeout)
    finally:
        release.set()
    for _ in range(200):
        if timeouts_module.abandoned_calls() == 0:
            break
        threading.Event().wait(0.01)


def test_the_deadline_copies_the_logging_context() -> None:
    from apps.core.logging import get_job_id, reset_job_id, set_job_id

    token = set_job_id("job-123")
    try:
        assert run_with_deadline(get_job_id, 5) == "job-123"
    finally:
        reset_job_id(token)
