"""Serializers for the client endpoints (issue #47)."""

from __future__ import annotations

from typing import ClassVar

from rest_framework import serializers

from apps.campaigns.models import Client, ClientStatus


class ClientSerializer(serializers.ModelSerializer[Client]):
    """A client. ``status`` and the timestamps are read-only: use ``archive/`` and ``restore/``.

    Name uniqueness (case-insensitive, among non-archived clients) is enforced by the service
    layer and reported as a ``name`` field error in the standard error envelope.
    """

    class Meta:
        model = Client
        fields = ("id", "name", "notes", "status", "archived_at", "created_at", "updated_at")
        read_only_fields = ("id", "status", "archived_at", "created_at", "updated_at")
        extra_kwargs: ClassVar[dict[str, dict[str, object]]] = {
            "notes": {"required": False, "allow_blank": True},
        }


class ClientListQuerySerializer(serializers.Serializer[dict[str, str]]):
    """Query parameters of the client list (validated by the view; documentation for OpenAPI)."""

    status = serializers.ChoiceField(
        choices=ClientStatus.choices,
        required=False,
        help_text="Only clients with this status. `archived` implies `archived=true` unless "
        "`archived` is given.",
    )
    archived = serializers.ChoiceField(
        choices=["false", "true", "all"],
        required=False,
        help_text="`false` (default) hides archived clients, `true` shows only archived "
        "ones, `all` shows both.",
    )
