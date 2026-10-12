"""Rate limiter, circuit breaker, their shared state, and ``resilient_execute`` (issue #78).

Time is a fake clock; "processes" are separate limiter or breaker objects over one shared cache
(a local cache for most tests, the real database cache ``q_stats`` where noted).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import pytest
from django.core.cache import caches

from apps.imports.providers.types import CompanySearchRequest
from apps.integrations.framework import (
    CompanyRef,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    SecretStr,
)
from apps.integrations.framework.base import RateLimit
from apps.integrations.resilience import (
    BreakerPolicy,
    CircuitBreaker,
    ProviderCircuitOpen,
    RateLimiter,
    Resilience,
    RetryPolicy,
    TimeoutPolicy,
    connection_status,
    resilient_execute,
)
from apps.integrations.resilience.breaker import CLOSED, HALF_OPEN, OPEN
from apps.integrations.resilience.ratelimit import limits_for
from apps.integrations.resilience.store import SharedState
from tests.resilience_helpers import FakeClock, ScriptedAdapter, local_state

EXAMPLE = CompanyRef(domain="acme.example.com")


# ================================================================== rate limiter


def limiter(
    clock: FakeClock, state: SharedState, scope: str = "c1:company_data:p", **limit: int
) -> RateLimiter:
    return RateLimiter(
        scope,
        RateLimit(**limit),
        state=state,
        clock=clock,
        sleep=clock.sleep,
        max_block=5.0,
        provider_name="p",
    )


def test_per_second_bucket_allows_a_burst_then_refills() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_second=3)
    assert [bool(rl.try_acquire()) for _ in range(4)] == [True, True, True, False]
    denied = rl.try_acquire()
    assert denied.allowed is False
    assert denied.retry_after == pytest.approx(1 / 3, abs=0.001)
    clock.advance(0.34)  # one token back
    assert rl.try_acquire().allowed is True
    assert rl.try_acquire().allowed is False
    clock.advance(10)  # refill is capped at the capacity, not accumulated
    assert [bool(rl.try_acquire()) for _ in range(4)] == [True, True, True, False]


def test_boundary_exactly_at_the_refill_instant() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_second=1)
    assert rl.try_acquire().allowed
    clock.advance(0.999)
    assert not rl.try_acquire().allowed
    clock.advance(0.001)
    assert rl.try_acquire().allowed


def test_all_windows_must_have_a_token_and_a_refusal_takes_nothing() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_second=5, per_minute=6)
    assert sum(rl.try_acquire().allowed for _ in range(5)) == 5  # burst of 5, minute has 1 left
    clock.advance(1.0)  # per second refilled, per minute only by 5 * 1/60 tokens
    first = rl.try_acquire()
    assert first.allowed  # the sixth of the minute
    second = rl.try_acquire()
    assert not second.allowed
    assert second.retry_after == pytest.approx(10 - 1 + 0.0, abs=0.5)  # minute rate: 1 per 10 s
    snap = rl.snapshot()
    assert {w["window"]: w["remaining"] for w in snap["windows"]} == {
        "per_second": 4,
        "per_minute": 0,
    }


def test_daily_limit() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_day=2)
    assert rl.try_acquire().allowed
    assert rl.try_acquire().allowed
    denied = rl.try_acquire()
    assert not denied.allowed
    assert denied.retry_after == pytest.approx(43_200, abs=1)  # half a day per token
    clock.advance(43_200)
    assert rl.try_acquire().allowed


def test_no_limit_never_limits_and_stores_nothing() -> None:
    clock, state = FakeClock(), local_state()
    rl = RateLimiter("s", None, state=state, clock=clock)
    assert all(rl.try_acquire().allowed for _ in range(1000))
    assert rl.snapshot() == {"enabled": False, "windows": []}
    assert state.get("rl:s") is None


def test_two_processes_share_one_budget() -> None:
    clock, state = FakeClock(), local_state()
    a = limiter(clock, state, per_minute=10)
    b = limiter(clock, state, per_minute=10)  # same scope, same cache: another worker process
    granted = [a.try_acquire().allowed if i % 2 else b.try_acquire().allowed for i in range(20)]
    assert sum(granted) == 10


def test_scopes_do_not_share_budgets() -> None:
    clock, state = FakeClock(), local_state()
    one = limiter(clock, state, scope="client-1:company_data:p", per_minute=1)
    two = limiter(clock, state, scope="client-2:company_data:p", per_minute=1)
    assert one.try_acquire().allowed
    assert two.try_acquire().allowed  # another client has its own key and budget
    assert not one.try_acquire().allowed


def test_budget_is_shared_through_the_database_cache(db: None) -> None:
    clock = FakeClock()
    state = SharedState()  # the real q_stats database cache, as in production
    a = limiter(clock, state, per_minute=5)
    b = limiter(clock, state, per_minute=5)
    results = [a.try_acquire().allowed for _ in range(3)] + [
        b.try_acquire().allowed for _ in range(4)
    ]
    assert sum(results) == 5
    assert caches["q_stats"].get("abm:resilience:rl:c1:company_data:p") is not None


def test_budget_holds_under_concurrent_threads(db: None) -> None:
    """Many threads on a locmem state still never exceed the budget (the lock is real)."""
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_minute=20)
    granted: list[bool] = []

    def worker() -> None:
        for _ in range(10):
            granted.append(rl.try_acquire().allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 80
    assert sum(granted) == 20


def test_acquire_waits_only_when_the_wait_fits_the_cap() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_second=1)
    rl.acquire()  # a token is free: no sleep
    assert clock.sleeps == []
    rl.acquire()  # needs 1 s: fits within the 5 s cap
    assert clock.sleeps == [1.0]


def test_acquire_never_blocks_longer_than_the_cap_and_reports_retry_after() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_minute=1)
    rl.acquire()
    with pytest.raises(ProviderRateLimited) as caught:
        rl.acquire()  # the next token is 60 s away: far beyond the 5 s cap
    assert clock.sleeps == []  # it did not hold the worker at all
    assert caught.value.retry_after == pytest.approx(60, abs=0.01)
    assert caught.value.provider_name == "p"
    assert caught.value.retryable is True


def test_acquire_timeout_is_capped_by_max_block() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_second=1)
    rl.acquire()
    with pytest.raises(ProviderRateLimited):
        rl.acquire(timeout=0.5)  # needs 1 s, caller allows 0.5
    rl.acquire(timeout=100)  # caller allows more than the cap, the 1 s wait still fits
    assert clock.sleeps == [1.0]


def test_clock_going_backwards_adds_no_tokens() -> None:
    clock, state = FakeClock(), local_state()
    rl = limiter(clock, state, per_second=1)
    assert rl.try_acquire().allowed
    clock.advance(-100)
    assert not rl.try_acquire().allowed


def test_limiter_validates_its_limits_and_cost() -> None:
    clock, state = FakeClock(), local_state()
    with pytest.raises(ValueError, match="positive"):
        limiter(clock, state, per_second=0)
    rl = limiter(clock, state, per_second=2)
    with pytest.raises(ValueError, match="at least 1"):
        rl.try_acquire(0)
    with pytest.raises(ValueError, match="exceeds"):
        rl.try_acquire(3)
    assert rl.try_acquire(2).allowed


def test_limits_come_from_the_adapter_and_settings_may_only_tighten(settings: Any) -> None:
    declared = RateLimit(per_second=10, per_minute=100)
    assert limits_for("p", declared) == declared
    settings.INTEGRATION_RESILIENCE = {
        "rate_limits": {"p": {"per_minute": 30, "per_day": 500, "per_second": 99}}
    }
    assert limits_for("p", declared) == RateLimit(per_second=10, per_minute=30, per_day=500)
    assert limits_for("other", None) is None
    assert limits_for("p", None) == RateLimit(per_second=99, per_minute=30, per_day=500)


def test_for_adapter_scopes_by_client_category_and_key() -> None:
    adapter = ScriptedAdapter()
    rl = RateLimiter.for_adapter(adapter, "client-9")
    assert rl.scope == "client-9:company_data:scripted_company"
    assert rl.limit == RateLimit(per_minute=60)


# ================================================================== circuit breaker

POLICY = BreakerPolicy(failure_threshold=3, window=30.0, cooldown=10.0)


def breaker(clock: FakeClock, state: SharedState, policy: BreakerPolicy = POLICY) -> CircuitBreaker:
    return CircuitBreaker("c1:company_data:p", policy, state=state, clock=clock, provider_name="p")


def trip(cb: CircuitBreaker, n: int = 3) -> None:
    for _ in range(n):
        cb.allow()
        cb.record_failure(ProviderUnavailable())


def test_opens_after_the_threshold_of_consecutive_failures() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb, 2)
    assert cb.current_state() == CLOSED
    cb.allow()
    cb.record_failure(ProviderTimeout())
    assert cb.current_state() == OPEN
    with pytest.raises(ProviderCircuitOpen) as caught:
        cb.allow()
    error = caught.value
    assert isinstance(error, ProviderUnavailable)  # the framework's own type and code
    assert error.code == "provider_unavailable"
    assert error.retryable is True
    assert error.retry_after == pytest.approx(10.0)


def test_a_success_resets_the_count() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb, 2)
    cb.record_success()
    trip(cb, 2)
    assert cb.current_state() == CLOSED


def test_failures_outside_the_window_are_forgotten() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb, 2)
    clock.advance(31)
    trip(cb, 2)
    assert cb.current_state() == CLOSED
    cb.record_failure(ProviderUnavailable())
    assert cb.current_state() == OPEN


@pytest.mark.parametrize(
    "error",
    [ProviderAuthError(), ProviderBadRequest("name"), ProviderRateLimited(3)],
)
def test_errors_that_are_not_outages_never_open_the_circuit(error: ProviderError) -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    assert cb.counts_as_failure(error) is False
    for _ in range(20):
        cb.allow()
        cb.record_failure(error)
    assert cb.current_state() == CLOSED


def test_rate_limited_can_be_counted_by_policy() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state, BreakerPolicy(failure_threshold=2, count_rate_limited=True))
    cb.record_failure(ProviderRateLimited(1))
    cb.record_failure(ProviderRateLimited(1))
    assert cb.current_state() == OPEN


def test_half_open_lets_one_probe_through_and_a_good_probe_closes() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb)
    clock.advance(9.9)
    with pytest.raises(ProviderCircuitOpen):
        cb.allow()
    clock.advance(0.2)  # cooled down
    assert cb.current_state() == HALF_OPEN
    cb.allow()  # the probe
    with pytest.raises(ProviderCircuitOpen):
        breaker(clock, state).allow()  # another worker is refused while the probe runs
    cb.record_success()
    assert cb.current_state() == CLOSED
    breaker(clock, state).allow()


def test_a_failed_probe_reopens_for_a_fresh_cooldown() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb)
    clock.advance(10)
    cb.allow()
    cb.record_failure(ProviderUnavailable())
    assert cb.current_state() == OPEN
    clock.advance(9)
    with pytest.raises(ProviderCircuitOpen) as caught:
        cb.allow()
    assert caught.value.retry_after == pytest.approx(1.0)
    clock.advance(1)
    cb.allow()
    cb.record_success()
    assert cb.current_state() == CLOSED


def test_a_probe_that_answers_with_a_non_outage_error_closes_the_circuit() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb)
    clock.advance(10)
    cb.allow()
    cb.record_failure(ProviderAuthError())  # the provider is up, our key is bad
    assert cb.current_state() == CLOSED


def test_a_probe_lease_expires_so_a_dead_worker_cannot_wedge_the_circuit() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb)
    clock.advance(10)
    cb.allow()  # probe granted, the worker dies and never reports
    with pytest.raises(ProviderCircuitOpen):
        cb.allow()
    clock.advance(10)  # the lease runs out
    cb.allow()  # a new probe


def test_a_released_probe_slot_can_be_used_by_the_next_caller() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb)
    clock.advance(10)
    cb.allow()
    cb.release_probe()
    cb.allow()


def test_state_is_shared_between_processes_and_scoped() -> None:
    clock, state = FakeClock(), local_state()
    a, b = breaker(clock, state), breaker(clock, state)
    trip(a)
    assert b.current_state() == OPEN
    other = CircuitBreaker("c2:company_data:p", POLICY, state=state, clock=clock)
    other.allow()  # another client is unaffected


def test_breaker_state_in_the_database_cache(db: None) -> None:
    clock, state = FakeClock(), SharedState()
    trip(breaker(clock, state))
    assert breaker(clock, state).current_state() == OPEN
    assert breaker(clock, state).snapshot()["state"] == "open"


def test_late_failures_while_open_do_not_extend_the_cooldown() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    trip(cb)
    clock.advance(5)
    cb.record_failure(ProviderUnavailable())  # a call that was already in flight
    clock.advance(5)
    assert cb.current_state() == HALF_OPEN


def test_breaker_policy_validates() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        BreakerPolicy(failure_threshold=0)
    with pytest.raises(ValueError, match="positive"):
        BreakerPolicy(cooldown=0)


def test_snapshot_reports_state_and_remaining_cooldown() -> None:
    clock, state = FakeClock(), local_state()
    cb = breaker(clock, state)
    assert cb.snapshot() == {
        "state": "closed",
        "recent_failures": 0,
        "failure_threshold": 3,
        "retry_after": 0.0,
    }
    trip(cb)
    clock.advance(4)
    snap = cb.snapshot()
    assert snap["state"] == "open"
    assert snap["retry_after"] == pytest.approx(6.0)


# ================================================================== resilient_execute

FAST = RetryPolicy(max_attempts=3, base_delay=1.0, jitter=0.0)


def make(
    script: list[ProviderError | None], clock: FakeClock, **parts: Any
) -> tuple[ScriptedAdapter, dict[str, Any]]:
    adapter = ScriptedAdapter(script=script)
    state = parts.pop("state", None) or local_state()
    kwargs: dict[str, Any] = {
        "policy": FAST,
        "limiter": parts.pop("limiter", None)
        or RateLimiter.for_adapter(adapter, "c1", state=state, clock=clock, sleep=clock.sleep),
        "breaker": parts.pop("breaker", None)
        or CircuitBreaker.for_adapter(adapter, "c1", policy=POLICY, state=state, clock=clock),
        "sleep": clock.sleep,
        "clock": clock,
        "timeouts": TimeoutPolicy(deadline=5),
    }
    kwargs.update(parts)
    return adapter, kwargs


def test_success_passes_the_framework_result_through() -> None:
    clock = FakeClock()
    adapter, kw = make([], clock)
    result = resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    assert result.provenance.provider == "scripted_company"
    assert result.usage.latency_ms is not None  # filled by the framework execute()
    assert adapter.attempts == 1


def test_simulated_429_is_retried_and_honours_retry_after() -> None:
    clock = FakeClock()
    adapter, kw = make([ProviderRateLimited(4.0), ProviderRateLimited(2.0)], clock)
    result = resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    assert result.data is not None
    assert adapter.attempts == 3
    assert clock.sleeps == [4.0, 2.0]  # the provider's numbers, not the computed backoff


def test_auth_errors_fail_fast_without_retries_and_do_not_trip_the_breaker() -> None:
    clock = FakeClock()
    adapter, kw = make([ProviderAuthError()] * 10, clock)
    for _ in range(5):
        with pytest.raises(ProviderAuthError):
            resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    assert adapter.attempts == 5  # one call each, never repeated
    assert clock.sleeps == []
    assert kw["breaker"].current_state() == CLOSED


def test_unexpected_exceptions_become_unavailable_through_execute_and_are_retried() -> None:
    clock = FakeClock()
    adapter = ScriptedAdapter()
    calls = {"n": 0}

    def flaky_op(company: CompanyRef) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("secret-ish detail that must not surface")
        return ScriptedAdapter.enrich_company(adapter, company)

    flaky_op.__name__ = "enrich_company"
    _, kw = make([], clock)
    resilient_execute(adapter, flaky_op, EXAMPLE, **kw)
    assert calls["n"] == 2


def test_exhausted_retries_raise_the_last_typed_error_and_feed_the_breaker() -> None:
    clock = FakeClock()
    adapter, kw = make([ProviderUnavailable()] * 20, clock)
    with pytest.raises(ProviderUnavailable):
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    assert adapter.attempts == 3
    # 3 counted failures = the threshold of POLICY: the circuit is open now
    assert kw["breaker"].current_state() == OPEN


def test_open_circuit_raises_unavailable_with_retry_after_and_never_calls_the_provider() -> None:
    clock = FakeClock()
    adapter, kw = make([ProviderUnavailable()] * 20, clock)
    with pytest.raises(ProviderUnavailable):
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    calls = adapter.attempts
    sleeps = list(clock.sleeps)
    with pytest.raises(ProviderCircuitOpen) as caught:
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    assert adapter.attempts == calls  # no call went out
    assert clock.sleeps == sleeps  # and nothing slept on it
    assert isinstance(caught.value, ProviderUnavailable)
    assert caught.value.retry_after is not None
    assert caught.value.retry_after > 0


def test_circuit_recovers_after_the_cooldown() -> None:
    clock = FakeClock()
    adapter, kw = make([ProviderUnavailable()] * 3, clock)
    with pytest.raises(ProviderUnavailable):
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    clock.advance(POLICY.cooldown)
    result = resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)  # the probe
    assert result.data is not None
    assert kw["breaker"].current_state() == CLOSED


def test_the_rate_limit_is_enforced_before_the_call_with_retry_after() -> None:
    clock = FakeClock()
    adapter = ScriptedAdapter()
    state = local_state()
    limiter = RateLimiter(
        "c1:company_data:scripted_company",
        RateLimit(per_minute=2),
        state=state,
        clock=clock,
        sleep=clock.sleep,
        max_block=1.0,
    )
    _, kw = make([], clock, state=state, limiter=limiter)
    kw["policy"] = RetryPolicy.no_retry()
    resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    with pytest.raises(ProviderRateLimited) as caught:
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    assert adapter.attempts == 2  # the third never reached the adapter
    assert caught.value.retry_after == pytest.approx(30, abs=0.1)


def test_a_rate_limited_half_open_probe_gives_the_slot_back() -> None:
    clock = FakeClock()
    adapter, kw = make([], clock)
    breaker = kw["breaker"]
    trip(breaker)
    clock.advance(POLICY.cooldown)
    kw["limiter"] = RateLimiter(
        "x", RateLimit(per_minute=1), state=local_state(), clock=clock, max_block=0
    )
    kw["limiter"].try_acquire()
    kw["policy"] = RetryPolicy.no_retry()
    with pytest.raises(ProviderRateLimited):
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
    breaker.allow()  # the probe slot was released, not left wedged


def test_a_hung_adapter_call_hits_the_hard_deadline() -> None:
    clock = FakeClock()
    release = threading.Event()
    adapter, kw = make([], clock)

    def hang(company: CompanyRef) -> Any:
        release.wait(30)

    hang.__name__ = "enrich_company"
    kw["policy"] = RetryPolicy.no_retry()
    kw["timeouts"] = TimeoutPolicy(deadline=0.05)
    try:
        with pytest.raises(ProviderTimeout):
            resilient_execute(adapter, hang, EXAMPLE, **kw)
    finally:
        release.set()


def test_unsupported_feature_is_refused_by_the_framework() -> None:
    clock = FakeClock()
    adapter, kw = make([], clock)
    with pytest.raises(ProviderBadRequest):
        resilient_execute(adapter, adapter.list_job_postings, EXAMPLE, **kw)  # not declared


def test_client_id_builds_the_shared_limiter_and_breaker(db: None) -> None:
    adapter = ScriptedAdapter(script=[ProviderUnavailable(), None])
    result = resilient_execute(
        adapter,
        adapter.enrich_company,
        EXAMPLE,
        client_id="client-1",
        sleep=lambda s: None,
        policy=FAST,
    )
    assert result.data is not None
    status = connection_status(adapter, "client-1")
    assert status["breaker"]["state"] == "closed"
    window = status["rate_limit"]["windows"][0]
    assert (window["limit"], window["remaining"]) == (60, 58)  # two attempts took two tokens


def test_operation_arguments_pass_through_search() -> None:
    clock = FakeClock()
    adapter, kw = make([], clock)
    result = resilient_execute(adapter, adapter.search, CompanySearchRequest(query="a"), **kw)
    assert result.data is not None


def test_resilience_bundle_for_adapter_uses_settings(settings: Any) -> None:
    adapter = ScriptedAdapter()
    bundle = Resilience.for_adapter(adapter, "c1")
    assert bundle.breaker is not None
    assert bundle.limiter is not None
    settings.INTEGRATION_RESILIENCE = {"breaker_enabled": False}
    assert Resilience.for_adapter(adapter, "c1").breaker is None


# ================================================================== no secrets


def test_logs_and_reprs_never_contain_credentials_or_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "YOUR_API_KEY"  # placeholder in the allow-listed shape
    clock = FakeClock()
    adapter = ScriptedAdapter(
        credentials={"api_key": SecretStr(secret)},
        script=[ProviderUnavailable(), ProviderRateLimited(1), None],
    )
    _, kw = make([], clock)
    with caplog.at_level(logging.DEBUG, logger="apps.integrations"):
        resilient_execute(adapter, adapter.enrich_company, EXAMPLE, **kw)
        for _ in range(3):  # drive the breaker open so it logs too
            kw["breaker"].record_failure(ProviderUnavailable())
    text = " ".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records)
    assert "provider call failed, retrying" in text  # codes and counts are logged
    assert "circuit opened" in text
    assert "acme.example.com" not in text  # arguments are not
    assert secret not in text
    for obj in (adapter, kw["breaker"], kw["limiter"], kw["policy"]):
        assert secret not in repr(obj)
        assert secret not in repr(vars(obj))
