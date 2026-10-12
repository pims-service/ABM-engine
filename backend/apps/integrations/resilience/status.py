"""Health and status of the resilience state, for the settings UI and ops (issue #78).

Pure, read-only functions: they never change a bucket or a circuit. A client-scoped endpoint for
the future settings UI (#77) can return ``connection_status(...)`` as is: it holds only a
state name, counts and seconds, never credentials, payloads or provider text.
"""

from __future__ import annotations

from typing import Any

from apps.integrations.framework.base import Adapter

from .breaker import CircuitBreaker
from .ratelimit import RateLimiter
from .timeouts import abandoned_calls


def connection_status(adapter: Adapter, client_id: object) -> dict[str, Any]:
    """Breaker and rate limit state of ``adapter`` for ``client_id``."""
    return {
        "provider": adapter.key,
        "category": adapter.category.value,
        "breaker": CircuitBreaker.for_adapter(adapter, client_id).snapshot(),
        "rate_limit": RateLimiter.for_adapter(adapter, client_id).snapshot(),
    }


def process_status() -> dict[str, Any]:
    """Per-process signals: calls that exceeded their hard deadline and are still running."""
    return {"abandoned_calls": abandoned_calls()}
