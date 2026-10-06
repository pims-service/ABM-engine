"""Login throttles: per client IP and per submitted email.

The per-email throttle slows password guessing against one account from many addresses; the
per-IP one slows guessing many accounts from one address. Emails are hashed in the cache key, so
no address is stored. Rates come from ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`` (scopes
``login`` and ``login_email``) and are read on every request, so they can be overridden in tests.

Counters live in Django's cache. The default local-memory cache is per process: with several
gunicorn workers the effective limit is multiplied, so production should point ``CACHES`` at a
shared backend. Behind a proxy set ``NUM_PROXIES`` (DRF) so the real client IP is used.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle


class _LoginThrottle(SimpleRateThrottle):
    def get_rate(self) -> str:
        rates = api_settings.DEFAULT_THROTTLE_RATES
        return str(rates[str(self.scope)])


class LoginIPThrottle(_LoginThrottle):
    scope = "login"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class LoginEmailThrottle(_LoginThrottle):
    scope = "login_email"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        data = request.data
        email = data.get("email") if isinstance(data, Mapping) else None
        if not isinstance(email, str) or not email.strip():
            return None
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
