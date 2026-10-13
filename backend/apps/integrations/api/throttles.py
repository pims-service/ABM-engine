"""Rate limit of the connection test action: a few tests per minute per connection.

Testing calls an external provider with the stored credentials, so repeated clicks (or a script)
must not hammer it or burn the provider's quota. The counter is keyed by connection id (not by
user), so two admins cannot double the budget. It lives in Django's cache; with several workers on
the default local-memory cache the effective limit is multiplied (see ``apps.accounts.throttles``).
The view applies it **after** the permission check on the object, so a user without the role can
never use up an admin's budget.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle


class ConnectionTestThrottle(SimpleRateThrottle):
    scope = "connection_test"
    rate = "5/min"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": str(view.kwargs.get("pk"))}
