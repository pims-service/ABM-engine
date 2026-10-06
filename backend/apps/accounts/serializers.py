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


class RefreshSerializer(TokenRefreshSerializer):
    """Refresh serializer that answers 401 (not 500) when the token's user was deleted."""

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        try:
            return super().validate(attrs)
        except User.DoesNotExist as exc:
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            ) from exc
