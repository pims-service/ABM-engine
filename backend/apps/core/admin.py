"""Django admin for jobs, the audit log and the third-party tables we expose (issue #54).

Jobs are created and moved by ``apps.core.jobs`` and audit rows are append-only, so nobody gets
add, change or delete buttons here. The token blacklist and the task queue tables are locked down
to superusers and view only (rules and the exclusion list: ``docs/admin.md``).
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django_q.models import Failure, OrmQ, Success
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .admin_base import ReadOnlyAdmin, ReadOnlyInline, SuperuserOnlyMixin
from .audit import redact_value
from .models import AuditLog, Job, JobItem


class JobItemInline(ReadOnlyInline):
    model = JobItem
    fields = ("subject_type", "subject_id", "status", "error", "started_at", "finished_at")
    readonly_fields = fields


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
    list_select_related = ("client", "campaign")
    search_fields = ("type", "client__name", "campaign__name")
    date_hierarchy = "created_at"
    inlines = (JobItemInline,)


@admin.register(JobItem)
class JobItemAdmin(ReadOnlyAdmin):
    list_display = ("job", "subject_type", "subject_id", "status", "client", "finished_at")
    list_filter = ("status", "subject_type", "client")
    list_select_related = ("job", "client")
    search_fields = ("subject_id", "job__type")
    date_hierarchy = "created_at"


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    """The stored diff is already redacted when written; it is redacted again on display, so a
    secret that reached the table some other way (a raw insert, an older row) still never shows."""

    list_display = ("created_at", "action", "object_type", "object_id", "actor", "client")
    list_filter = ("action", "object_type", "client")
    list_select_related = ("actor", "client")
    search_fields = ("object_id", "request_id", "actor__email")
    date_hierarchy = "created_at"
    fields = (
        "id",
        "created_at",
        "action",
        "object_type",
        "object_id",
        "actor",
        "client",
        "request_id",
        "before_redacted",
        "after_redacted",
    )

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return self.fields

    @staticmethod
    def _render(value: Any) -> SafeString | str:
        if value is None:
            return "-"
        return format_html(
            "<pre>{}</pre>", json.dumps(redact_value(value), indent=2, sort_keys=True)
        )

    @admin.display(description="Before (redacted)")
    def before_redacted(self, obj: AuditLog) -> SafeString | str:
        return self._render(obj.before)

    @admin.display(description="After (redacted)")
    def after_redacted(self, obj: AuditLog) -> SafeString | str:
        return self._render(obj.after)


# ------------------------------------------------------------------ third-party tables


def _replace(model: type, admin_class: type[admin.ModelAdmin]) -> None:  # type: ignore[type-arg]
    """Swap a model's stock admin for ours (a no-op for the unregister if it was never added)."""
    if admin.site.is_registered(model):
        admin.site.unregister(model)
    admin.site.register(model, admin_class)


class OutstandingTokenAdmin(SuperuserOnlyMixin, ReadOnlyAdmin):
    """Refresh token bookkeeping. The stock admin shows the full ``token`` string, which is a
    live credential until it expires, so it is left out here."""

    list_display = ("jti", "user", "created_at", "expires_at")
    list_select_related = ("user",)
    search_fields = ("jti", "user__email")
    date_hierarchy = "created_at"
    fields = ("id", "jti", "user", "created_at", "expires_at")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return self.fields


class BlacklistedTokenAdmin(SuperuserOnlyMixin, ReadOnlyAdmin):
    """Revoked refresh tokens. View only: deleting a row here would un-revoke a token."""

    list_display = ("token_jti", "token_user", "blacklisted_at")
    list_select_related = ("token__user",)
    search_fields = ("token__jti", "token__user__email")
    date_hierarchy = "blacklisted_at"
    fields = ("id", "token_jti", "token_user", "blacklisted_at")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return self.fields

    @admin.display(description="jti", ordering="token__jti")
    def token_jti(self, obj: BlacklistedToken) -> str:
        return obj.token.jti

    @admin.display(description="user", ordering="token__user__email")
    def token_user(self, obj: BlacklistedToken) -> object:
        return obj.token.user


_replace(OutstandingToken, OutstandingTokenAdmin)
_replace(BlacklistedToken, BlacklistedTokenAdmin)


def _lock_down_task_admin(model: type) -> None:
    """Django-Q's task tables hold function arguments and results. Superusers may look, nobody
    may edit, and the stock "resubmit to queue" action is removed (it would re-run a function)."""
    if not admin.site.is_registered(model):
        return
    stock = type(admin.site._registry[model])

    class Locked(SuperuserOnlyMixin, ReadOnlyAdmin, stock):  # type: ignore[valid-type, misc]
        actions = None

        def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
            return tuple(f.name for f in self.model._meta.fields)

    Locked.__name__ = f"Locked{stock.__name__}"
    _replace(model, Locked)


_lock_down_task_admin(Success)
_lock_down_task_admin(Failure)
if admin.site.is_registered(OrmQ):  # queued payloads: see ADMIN_EXCLUDED_MODELS
    admin.site.unregister(OrmQ)


@admin.register(LogEntry)
class LogEntryAdmin(SuperuserOnlyMixin, ReadOnlyAdmin):
    """Django's own record of what staff did in this admin."""

    list_display = ("action_time", "user", "content_type", "object_repr", "action_flag")
    list_filter = ("action_flag", "content_type")
    list_select_related = ("user", "content_type")
    search_fields = ("object_repr", "user__email")
    date_hierarchy = "action_time"
