"""Django admin for companies. Research snapshots and data sources are append-only: view only.

Companies are archived, never deleted, and created through ``services.create_company`` (the
admin cannot dedupe), so there is no add or delete button.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import Company, CompanyResearch, DataSource


class ReadOnlyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "domain", "campaign", "client", "status", "input_source", "archived_at")
    list_filter = ("status", "input_source", "client")
    search_fields = ("name", "domain", "website")
    readonly_fields = tuple(f.name for f in Company._meta.fields if f.name != "name")
    actions = ("archive_selected", "restore_selected")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.action(description="Archive selected companies")
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[Company]) -> None:
        for company in queryset:
            company.archive()

    @admin.action(description="Restore selected companies")
    def restore_selected(self, request: HttpRequest, queryset: QuerySet[Company]) -> None:
        for company in queryset:
            company.restore()


@admin.register(CompanyResearch)
class CompanyResearchAdmin(ReadOnlyAdmin):
    list_display = ("company", "researched_at", "data_source", "industry", "employee_count")
    list_filter = ("client",)
    search_fields = ("company__name", "company__domain")
    ordering = ("-researched_at", "-created_at")


@admin.register(DataSource)
class DataSourceAdmin(ReadOnlyAdmin):
    list_display = ("name", "type", "client", "retrieved_at", "evidence_date")
    list_filter = ("type", "client")
    search_fields = ("name", "url")
