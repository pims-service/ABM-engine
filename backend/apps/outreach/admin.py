"""Django admin for outreach: everything is view only. Create and change rows through
``apps.outreach.services`` (angles and activities are append-only, messages only move forward)."""

from __future__ import annotations

from django.contrib import admin

from apps.companies.admin import ReadOnlyAdmin

from .models import Activity, Message, OutreachAngle


@admin.register(OutreachAngle)
class OutreachAngleAdmin(ReadOnlyAdmin):
    list_display = ("angle", "company", "client", "created_at")
    list_filter = ("client",)
    search_fields = ("angle", "company__name")
    ordering = ("-created_at",)


@admin.register(Message)
class MessageAdmin(ReadOnlyAdmin):
    list_display = (
        "company",
        "contact",
        "channel",
        "language",
        "status",
        "is_followup",
        "created_at",
    )
    list_filter = ("channel", "status", "language", "client")
    search_fields = ("company__name", "body", "subject")
    ordering = ("-created_at",)


@admin.register(Activity)
class ActivityAdmin(ReadOnlyAdmin):
    list_display = ("type", "company", "campaign", "actor", "created_at")
    list_filter = ("type", "client")
    search_fields = ("company__name",)
    ordering = ("-created_at",)
