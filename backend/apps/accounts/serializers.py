from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from .models import User


class UserSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ("id", "email", "name", "is_staff", "last_login", "created_at")
        read_only_fields = fields


class RefreshTokenSerializer(serializers.Serializer[dict[str, str]]):
    """Body of `refresh/` and `logout/` (documentation only; the views read the raw body)."""

    refresh = serializers.CharField(
        required=False,
        help_text="Refresh token. May be omitted when the refresh cookie is enabled.",
    )


class TokenResponseSerializer(serializers.Serializer[dict[str, str]]):
    """Tokens returned by `login/` and `refresh/` (documentation only)."""

    access = serializers.CharField(help_text="Short-lived JWT access token (Bearer).")
    refresh = serializers.CharField(
        required=False,
        help_text="Refresh token. Omitted when the refresh cookie is enabled "
        "(it is then delivered as an httpOnly cookie).",
    )


class RefreshSerializer(TokenRefreshSerializer):
    """Refresh serializer that answers 401 (not 500) when the token's user was deleted."""

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        try:
            return super().validate(attrs)
        except User.DoesNotExist as exc:
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            ) from exc
