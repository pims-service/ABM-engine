"""Resilience layer for provider calls (issue #78, ADR 0014): retries, timeouts, rate limiting and
a circuit breaker, shared by every adapter and by the job base (``apps.integrations.tasks``).

Pure Python plus Django's cache; no models, no migrations, no network, no vendor SDK. State that
must be shared between worker processes (rate limit buckets, circuit state) lives in the database
cache (Postgres), never in Redis. See ``docs/integrations/resilience.md``.
"""

from .breaker import BreakerPolicy, CircuitBreaker
from .compose import Resilience, resilient_execute
from .errors import ProviderCircuitOpen
from .ratelimit import RateDecision, RateLimiter
from .retry import RetryPolicy, call_with_retry
from .status import connection_status, process_status
from .timeouts import TimeoutPolicy, run_with_deadline

__all__ = [
    "BreakerPolicy",
    "CircuitBreaker",
    "ProviderCircuitOpen",
    "RateDecision",
    "RateLimiter",
    "Resilience",
    "RetryPolicy",
    "TimeoutPolicy",
    "call_with_retry",
    "connection_status",
    "process_status",
    "resilient_execute",
    "run_with_deadline",
]
