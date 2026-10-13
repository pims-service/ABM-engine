"""Errors raised by the resilience layer itself (issue #78, ADR 0014).

``ProviderCircuitOpen`` is a ``ProviderError`` subclass that keeps the existing code, so callers,
jobs and the API only ever deal with the framework's error family.
"""

from __future__ import annotations

from apps.integrations.framework.errors import ProviderUnavailable


class ProviderCircuitOpen(ProviderUnavailable):
    """Calls to this provider are paused by the circuit breaker (code ``provider_unavailable``).

    ``retry_after`` is the remaining cool-down in seconds. It is retryable for a *job* (re-queue
    after the delay) but ``call_with_retry`` never sleeps on it inline.
    """

    message = "Calls to this data provider are paused after repeated failures. Try again shortly."
