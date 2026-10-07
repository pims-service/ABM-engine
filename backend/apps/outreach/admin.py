"""Django admin for outreach: everything is view only (issue #54). Create and change rows through
``apps.outreach.services`` (angles and activities are append-only, messages only move forward,
the evidence link tables are inserted with their angle or message)."""

from __future__ import annotations

from django.contrib import admin

from apps.core.admin_base import ReadOnlyAdmin, ReadOnlyInline

from .models import (
    Activity,
    AngleSignal,
    AngleSource,
    Message,
    MessageSignal,
    MessageSource,
    OutreachAngle,
)


class AngleSignalInline(ReadOnlyInline):
    model = AngleSignal
    fields = ("signal",)
    readonly_fields = fields


class AngleSourceInline(ReadOnlyInline):
    model = AngleSource
    fields = ("data_source",)
    readonly_fields = fields


class MessageSignalInline(ReadOnlyInline):
    model = MessageSignal
    fields = ("signal",)
    readonly_fields = fields


class MessageSourceInline(ReadOnlyInline):
    model = MessageSource
    fields = ("data_source",)
    readonly_fields = fields


@admin.register(OutreachAngle)
class OutreachAngleAdmin(ReadOnlyAdmin):
    list_display = ("angle", "company", "client", "created_at")
    list_filter = ("client",)
    list_select_related = ("company", "client")
    search_fields = ("angle", "company__name")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    inlines = (AngleSignalInline, AngleSourceInline)


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
    list_select_related = ("company", "contact")
    search_fields = ("company__name", "body", "subject")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    inlines = (MessageSignalInline, MessageSourceInline)


@admin.register(Activity)
class ActivityAdmin(ReadOnlyAdmin):
    list_display = ("type", "company", "campaign", "actor", "created_at")
    list_filter = ("type", "client")
    list_select_related = ("company", "campaign", "actor")
    search_fields = ("company__name",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(AngleSignal)
class AngleSignalAdmin(ReadOnlyAdmin):
    list_display = ("angle", "signal", "client")
    list_filter = ("client",)
    list_select_related = ("angle", "signal", "client")


@admin.register(AngleSource)
class AngleSourceAdmin(ReadOnlyAdmin):
    list_display = ("angle", "data_source", "client")
    list_filter = ("client",)
    list_select_related = ("angle", "data_source", "client")


@admin.register(MessageSignal)
class MessageSignalAdmin(ReadOnlyAdmin):
    list_display = ("message", "signal", "client")
    list_filter = ("client",)
    list_select_related = ("message", "signal", "client")


@admin.register(MessageSource)
class MessageSourceAdmin(ReadOnlyAdmin):
    list_display = ("message", "data_source", "client")
    list_filter = ("client",)
    list_select_related = ("message", "data_source", "client")
