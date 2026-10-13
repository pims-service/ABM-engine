"""Settings for the resilience layer (issue #78).

Everything is optional. Override single values in ``settings.INTEGRATION_RESILIENCE`` (a dict);
missing keys fall back to ``DEFAULTS``. No environment variables are involved: these are
tuning knobs for developers, not deployment settings.

    INTEGRATION_RESILIENCE = {
        "retry_max_attempts": 4,
        "rate_limits": {"hunter": {"per_minute": 300}},   # stricter than the adapter declares
    }
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from django.conf import settings

#: Name of the Django cache used for limiter and breaker state. It must be shared by every
#: process (the database cache ``q_stats`` is; ``locmem`` is per process: dev and tests only).
DEFAULT_CACHE_ALIAS: Final = "q_stats"

DEFAULTS: Final[Mapping[str, Any]] = {
    "cache_alias": DEFAULT_CACHE_ALIAS,
    # retry
    "retry_max_attempts": 3,
    "retry_base_delay": 0.5,  # seconds
    "retry_factor": 2.0,
    "retry_max_delay": 8.0,  # cap of the computed backoff
    "retry_jitter": 0.25,  # fraction of each delay that may be removed at random (0 to 1)
    "retry_max_retry_after": 15.0,  # a longer Retry-After is not slept inline: the caller re-queues
    # timeouts
    "connect_timeout": 3.05,
    "read_timeout": 30.0,
    "hard_deadline": 120.0,  # 0 turns the in-process deadline wrapper off
    "max_abandoned_calls": 8,  # hung calls tolerated per process before refusing new ones
    # rate limiter
    "rate_limit_max_block": 5.0,  # longest acquire() may sleep before raising ProviderRateLimited
    "rate_limits": {},  # adapter key -> {"per_second": n, "per_minute": n, "per_day": n}
    # circuit breaker
    "breaker_enabled": True,
    "breaker_failure_threshold": 5,  # consecutive counted failures that open the circuit
    "breaker_window": 60.0,  # ... within this many seconds
    "breaker_cooldown": 30.0,  # open for this long before one probe call is allowed
    "breaker_count_rate_limited": False,  # a 429 is flow control, not an outage
}


def get(name: str) -> Any:
    """One effective setting (the override in ``INTEGRATION_RESILIENCE`` or its default)."""
    if name not in DEFAULTS:
        raise KeyError(f"Unknown resilience setting {name!r}.")
    overrides: Mapping[str, Any] = getattr(settings, "INTEGRATION_RESILIENCE", None) or {}
    return overrides.get(name, DEFAULTS[name])


def user_agent() -> str:
    """The descriptive ``User-Agent`` adapters send (no contact data, no secrets)."""
    return str(getattr(settings, "INTEGRATION_USER_AGENT", "") or "ABMEngine/1.0 (+integrations)")
