"""Optional httpOnly cookie delivery of the refresh token (for the Next.js BFF).

Off by default: API clients get ``{"access", "refresh"}`` in the JSON body and send the refresh
token back in the body. With ``AUTH_REFRESH_COOKIE_ENABLED`` the refresh token is instead set as
an httpOnly cookie and left out of the body, and the refresh and logout endpoints read it from the
cookie when the request body has none (a body value still wins, so API clients keep working).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework.response import Response
from rest_framework_simplejwt.settings import api_settings


def cookie_enabled() -> bool:
    return bool(settings.AUTH_REFRESH_COOKIE_ENABLED)


def set_refresh_cookie(response: Response, refresh: str) -> None:
    response.set_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        refresh,
        max_age=int(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )


def deliver_tokens(response: Response) -> Response:
    """In cookie mode move ``refresh`` from the response body into the cookie."""
    if cookie_enabled() and isinstance(response.data, dict) and "refresh" in response.data:
        data: dict[str, Any] = response.data
        set_refresh_cookie(response, str(data.pop("refresh")))
    return response
