"""Django admin for jobs and the audit log: view only.

Jobs are created and moved by ``apps.core.jobs`` and audit rows are append-only, so nobody gets
add, change or delete buttons here.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import AuditLog, Job, JobItem


class ReadOnlyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest, *args: Any) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class JobItemInline(admin.TabularInline):  # type: ignore[type-arg]
    model = JobItem
    fields = ("subject_type", "subject_id", "status", "error", "started_at", "finished_at")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Job)
class JobAdmin(ReadOnlyAdmin):
    list_display = (
        "type",
        "client",
        "campaign",
        "status",
        "done_count",
        "failed_count",
        "total_count",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "type", "client")
    search_fields = ("type", "client__name", "campaign__name")
    inlines = (JobItemInline,)


@admin.register(JobItem)
class JobItemAdmin(ReadOnlyAdmin):
    list_display = ("job", "subject_type", "subject_id", "status", "client", "finished_at")
    list_filter = ("status", "subject_type", "client")


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "object_type", "object_id", "actor", "client")
    list_filter = ("action", "object_type", "client")
    search_fields = ("object_id", "request_id")
    date_hierarchy = "created_at"
