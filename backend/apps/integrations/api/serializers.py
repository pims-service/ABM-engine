"""Serializers for the integration connection endpoints (issue #76).

Credentials are **write-only**: they are accepted on create and update and never returned. A
response carries ``has_credentials`` and ``credentials_hint`` (masked, e.g. ``****abcd``, and
only ``****`` for short secrets). The model's ``credentials`` column is not a serializer field.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.integrations.models import (
    PROVIDER_KEY_PATTERN,
    ConnectionStatus,
    ConnectionTestStatus,
    IntegrationCategory,
    IntegrationConnection,
)
from apps.integrations.services import MAX_CREDENTIAL_VALUE_LENGTH


class ConnectionSerializer(serializers.ModelSerializer[IntegrationConnection]):
    """A connection as returned by the API. Never contains a secret."""

    has_credentials = serializers.BooleanField(
        read_only=True, help_text="True when credentials are stored. Their values are never shown."
    )
    credentials_hint = serializers.DictField(
        child=serializers.CharField(),
        read_only=True,
        help_text="Masked hint per credential name: `****abcd` (last 4 characters) for long "
        "secrets, `****` for short ones.",
    )

    class Meta:
        model = IntegrationConnection
        fields = (
            "id",
            "client",
            "category",
            "provider",
            "name",
            "status",
            "config",
            "has_credentials",
            "credentials_hint",
            "credentials_updated_at",
            "last_tested_at",
            "last_test_status",
            "last_test_message",
            "archived_at",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class StrictSecretField(serializers.CharField):
    """A string only: numbers and booleans are rejected instead of coerced."""

    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


def _credentials_field() -> serializers.DictField:
    return serializers.DictField(
        child=StrictSecretField(
            max_length=MAX_CREDENTIAL_VALUE_LENGTH,
            trim_whitespace=True,
            style={"input_type": "password"},
        ),
        write_only=True,
        required=False,
        allow_empty=False,
        help_text='Write-only secrets by name, e.g. `{"api_key": "..."}`. On update they '
        "replace all stored credentials. Never returned.",
    )


class ConnectionCreateSerializer(serializers.Serializer[dict[str, Any]]):
    client = serializers.UUIDField(help_text="The client this connection belongs to.")
    category = serializers.ChoiceField(choices=IntegrationCategory.choices)
    provider = serializers.RegexField(
        PROVIDER_KEY_PATTERN,
        max_length=64,
        help_text="Provider key, e.g. `openai`. Lower-case letters, digits and underscores.",
    )
    name = serializers.CharField(max_length=200, trim_whitespace=True)
    status = serializers.ChoiceField(
        choices=[ConnectionStatus.ACTIVE.value, ConnectionStatus.DISABLED.value],
        required=False,
        default=ConnectionStatus.ACTIVE.value,
    )
    config = serializers.DictField(
        required=False, help_text="Non-secret settings (JSON object). Secrets are rejected here."
    )
    credentials = _credentials_field()


class ConnectionUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    """Category and provider are fixed: create a new connection to switch provider."""

    name = serializers.CharField(max_length=200, trim_whitespace=True, required=False)
    config = serializers.DictField(
        required=False, help_text="Replaces the whole config. Secrets are rejected here."
    )
    credentials = _credentials_field()


class ConnectionListQuerySerializer(serializers.Serializer[dict[str, str]]):
    """Query parameters of the list (validated by the view; documentation for OpenAPI)."""

    client = serializers.UUIDField(required=False, help_text="Only this client's connections.")
    category = serializers.ChoiceField(choices=IntegrationCategory.choices, required=False)
    provider = serializers.CharField(required=False, max_length=64)
    status = serializers.ChoiceField(choices=ConnectionStatus.choices, required=False)
    test_status = serializers.ChoiceField(choices=ConnectionTestStatus.choices, required=False)
    archived = serializers.ChoiceField(
        choices=["false", "true", "all"],
        required=False,
        help_text="`false` (default) hides archived connections, `true` shows only them.",
    )
