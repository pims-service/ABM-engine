"""Django admin for integration connections: read-only and superuser-only (issue #76).

Connections change through ``apps.integrations.services`` (audited, secrets validated), so the
admin only looks. The ``credentials`` column is not listed anywhere; a superuser sees whether
credentials exist and the masked hint (``****abcd``), nothing more.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.core.admin_base import ReadOnlyAdmin, SuperuserOnlyMixin

from .models import CachedResponse, IntegrationConnection, ProviderCall, RawPayload, UsageBudget


@admin.register(IntegrationConnection)
class IntegrationConnectionAdmin(SuperuserOnlyMixin, ReadOnlyAdmin):
    list_display = (
        "name",
        "client",
        "category",
        "provider",
        "status",
        "has_credentials_flag",
        "last_test_status",
        "archived_at",
    )
    list_filter = ("category", "status", "provider")
    list_select_related = ("client",)
    search_fields = ("name", "provider", "client__name")
    date_hierarchy = "created_at"
    fields = (
        "id",
        "client",
        "category",
        "provider",
        "name",
        "status",
        "config",
        "has_credentials_flag",
        "credentials_hint",
        "credentials_updated_at",
        "last_tested_at",
        "last_test_status",
        "last_test_message",
        "created_by",
        "archived_at",
        "created_at",
        "updated_at",
    )

    def get_readonly_fields(self, request: Any, obj: Any = None) -> tuple[str, ...]:
        return tuple(self.fields)

    @admin.display(description="Credentials set", boolean=True)
    def has_credentials_flag(self, obj: IntegrationConnection) -> bool:
        return obj.has_credentials


# ------------------------------------------------------------------ usage and cache (#79, #80)


class _SuperuserReadOnly(SuperuserOnlyMixin, ReadOnlyAdmin):
    """View only, superusers only. Every shown field is read only."""

    def get_readonly_fields(self, request: Any, obj: Any = None) -> tuple[str, ...]:
        return tuple(str(f) for f in self.fields or ())


@admin.register(ProviderCall)
class ProviderCallAdmin(_SuperuserReadOnly):
    list_display = (
        "created_at",
        "client",
        "provider",
        "operation",
        "status",
        "error_code",
        "cache_hit",
        "credits",
        "cost",
        "currency",
    )
    list_filter = ("status", "cache_hit", "category", "provider")
    list_select_related = ("client",)
    search_fields = ("provider", "operation", "request_id", "client__name")
    date_hierarchy = "created_at"
    fields = (
        "id",
        "client",
        "connection",
        "campaign",
        "job",
        "triggered_by",
        "category",
        "provider",
        "operation",
        "status",
        "error_code",
        "latency_ms",
        "credits",
        "cost",
        "currency",
        "input_tokens",
        "output_tokens",
        "model",
        "cache_hit",
        "request_id",
        "created_at",
    )


@admin.register(UsageBudget)
class UsageBudgetAdmin(_SuperuserReadOnly):
    list_display = (
        "connection",
        "client",
        "credit_limit",
        "cost_limit",
        "currency",
        "warn_at_percent",
        "hard_stop",
    )
    list_select_related = ("client", "connection")
    fields = (
        "id",
        "client",
        "connection",
        "credit_limit",
        "cost_limit",
        "currency",
        "warn_at_percent",
        "hard_stop",
        "updated_by",
        "created_at",
        "updated_at",
    )


@admin.register(CachedResponse)
class CachedResponseAdmin(_SuperuserReadOnly):
    """The payload is not shown: it may hold personal data. Size and expiry are enough here."""

    list_display = ("provider", "operation", "client", "size_bytes", "created_at", "expires_at")
    list_filter = ("provider", "category")
    list_select_related = ("client",)
    fields = (
        "id",
        "client",
        "connection",
        "category",
        "provider",
        "operation",
        "cache_key",
        "size_bytes",
        "created_at",
        "expires_at",
    )


@admin.register(RawPayload)
class RawPayloadAdmin(_SuperuserReadOnly):
    """The payload is not shown: it may hold personal data. Size and expiry are enough here."""

    list_display = ("call", "client", "size_bytes", "created_at", "expires_at")
    list_select_related = ("client", "call")
    fields = ("id", "client", "call", "size_bytes", "created_at", "expires_at")
