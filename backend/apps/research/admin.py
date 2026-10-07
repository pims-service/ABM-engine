"""Django admin for research. Signals are append-only: view only (create via the service)."""

from __future__ import annotations

from django.contrib import admin

from apps.companies.admin import ReadOnlyAdmin

from .models import AIRecommendation, HumanDecision, ICPAssessment, Signal


@admin.register(Signal)
class SignalAdmin(ReadOnlyAdmin):
    list_display = ("type", "company", "event_date", "detected_at", "expires_at", "client")
    list_filter = ("type", "client")
    search_fields = ("company__name", "evidence")
    ordering = ("-event_date", "-detected_at")


@admin.register(ICPAssessment)
class ICPAssessmentAdmin(ReadOnlyAdmin):
    list_display = ("company", "fit", "campaign_profile", "model_name", "created_at")
    list_filter = ("fit", "client")
    search_fields = ("company__name",)


@admin.register(AIRecommendation)
class AIRecommendationAdmin(ReadOnlyAdmin):
    list_display = ("company", "status", "model_name", "created_at")
    list_filter = ("status", "client")
    search_fields = ("company__name",)


@admin.register(HumanDecision)
class HumanDecisionAdmin(ReadOnlyAdmin):
    list_display = ("company", "decision", "decided_by", "decided_at")
    list_filter = ("decision", "client")
    search_fields = ("company__name", "note")
