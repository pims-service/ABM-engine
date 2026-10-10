"""Django admin for research (issue #54). Signals, assessments, recommendations and decisions are
append-only: view only (create them through ``apps.research.services``)."""

from __future__ import annotations

from django.contrib import admin

from apps.core.admin_base import ReadOnlyAdmin

from .models import AIRecommendation, HumanDecision, ICPAssessment, Signal


@admin.register(Signal)
class SignalAdmin(ReadOnlyAdmin):
    list_display = ("type", "company", "event_date", "detected_at", "expires_at", "client")
    list_filter = ("type", "client")
    list_select_related = ("company", "client")
    search_fields = ("company__name", "evidence")
    date_hierarchy = "event_date"
    ordering = ("-event_date", "-detected_at")


@admin.register(ICPAssessment)
class ICPAssessmentAdmin(ReadOnlyAdmin):
    list_display = ("company", "fit", "campaign_profile", "model_name", "created_at")
    list_filter = ("fit", "client")
    list_select_related = ("company", "campaign_profile")
    search_fields = ("company__name",)
    date_hierarchy = "created_at"


@admin.register(AIRecommendation)
class AIRecommendationAdmin(ReadOnlyAdmin):
    list_display = ("company", "status", "model_name", "created_at")
    list_filter = ("status", "client")
    list_select_related = ("company",)
    search_fields = ("company__name",)
    date_hierarchy = "created_at"


@admin.register(HumanDecision)
class HumanDecisionAdmin(ReadOnlyAdmin):
    list_display = ("company", "decision", "decided_by", "decided_at")
    list_filter = ("decision", "client")
    list_select_related = ("company", "decided_by")
    search_fields = ("company__name", "note")
    date_hierarchy = "decided_at"
