"""Auth endpoints under ``/api/v1/auth/``: login, refresh, logout, me."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .cookies import clear_refresh_cookie, cookie_enabled, deliver_tokens
from .models import User
from .serializers import RefreshSerializer, UserSerializer
from .throttles import LoginEmailThrottle, LoginIPThrottle


class LoginView(TokenObtainPairView):
    """Exchange email and password for an access and refresh token.

    Every failure (unknown email, wrong password, inactive user) returns the same generic 401.
    """

    throttle_classes = (LoginIPThrottle, LoginEmailThrottle)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return deliver_tokens(super().post(request, *args, **kwargs))


class _RefreshTokenView(APIView):
    """Base for views that take a refresh token from the body, or from the cookie if enabled."""

    permission_classes = (AllowAny,)
    authentication_classes = ()  # the refresh token is the credential

    def get_authenticate_header(self, request: Request) -> str:
        # Without this DRF would turn token errors (401) into 403 for views with no authenticators.
        return 'Bearer realm="api"'

    @staticmethod
    def get_refresh_token(request: Request) -> str:
        data = request.data
        token = data.get("refresh") if isinstance(data, Mapping) else None
        if not token and cookie_enabled():
            token = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME)
        return token if isinstance(token, str) else ""


class RefreshView(_RefreshTokenView):
    """Rotate: returns a new access token and a new refresh token, blacklisting the old one."""

    def post(self, request: Request) -> Response:
        serializer = RefreshSerializer(data={"refresh": self.get_refresh_token(request)})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        return deliver_tokens(Response(serializer.validated_data, status=status.HTTP_200_OK))

    def finalize_response(self, request: Request, response: Response, *a: Any, **kw: Any) -> Any:
        response = super().finalize_response(request, response, *a, **kw)
        if cookie_enabled() and response.status_code == status.HTTP_401_UNAUTHORIZED:
            clear_refresh_cookie(response)  # a rejected cookie is useless; drop it
        return response


class LogoutView(_RefreshTokenView):
    """Blacklist the refresh token (and clear the cookie). Access tokens expire on their own."""

    def post(self, request: Request) -> Response:
        token = self.get_refresh_token(request)
        if not token:
            raise ValidationError({"refresh": ["This field is required."]})
        try:
            RefreshToken(token).blacklist()  # type: ignore[arg-type]
        except TokenError as exc:  # malformed, expired or already blacklisted
            raise InvalidToken(exc.args[0]) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    def finalize_response(self, request: Request, response: Response, *a: Any, **kw: Any) -> Any:
        response = super().finalize_response(request, response, *a, **kw)
        if cookie_enabled():
            clear_refresh_cookie(response)
        return response


class MeView(APIView):
    """The authenticated user."""

    def get(self, request: Request) -> Response:
        return Response(UserSerializer(cast(User, request.user)).data)
