"""Django admin for integration connections: read-only and superuser-only (issue #76).

Connections change through ``apps.integrations.services`` (audited, secrets validated), so the
admin only looks. The ``credentials`` column is not listed anywhere; a superuser sees whether
credentials exist and the masked hint (``****abcd``), nothing more.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.core.admin_base import ReadOnlyAdmin, SuperuserOnlyMixin

from .models import IntegrationConnection


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
