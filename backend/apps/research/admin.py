"""Django admin for research. Signals are append-only: view only (create via the service)."""

from __future__ import annotations

from django.contrib import admin

from apps.companies.admin import ReadOnlyAdmin

from .models import Signal


@admin.register(Signal)
class SignalAdmin(ReadOnlyAdmin):
    list_display = ("type", "company", "event_date", "detected_at", "expires_at", "client")
    list_filter = ("type", "client")
    search_fields = ("company__name", "evidence")
    ordering = ("-event_date", "-detected_at")
