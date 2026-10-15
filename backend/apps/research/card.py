"""The account research card (Brief section 10): everything a reviewer needs on one page.

``build_card(company)`` gathers the latest of each record. Nothing is computed with a model here
and nothing is invented: a fact that was not found is ``None``, and what needs a data provider we
do not have yet is listed in ``gaps`` so the screen can say so.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.companies.analysis import latest_analysis_jobs
from apps.companies.models import Company, CompanyResearch
from apps.outreach.models import Activity, ActivityType, OutreachAngle
from apps.outreach.services import FeedbackKind, FeedbackTargetType
from apps.research.models import (
    AIRecommendation,
    HumanDecision,
    ICPAssessment,
    Signal,
)
from apps.research.services import company_trigger_state

GAP_HEADCOUNT = (
    "Sales, business development and marketing headcount, and 3, 6 and 12 month growth, need a "
    "licensed company-data provider. None is connected yet."
)
GAP_BUYERS = "Buyer ranking needs a people-data provider. None is connected yet."


class SourceSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    url = serializers.CharField(allow_blank=True)
    retrieved_at = serializers.DateTimeField()


class CardCompanySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    website = serializers.CharField(allow_blank=True)
    domain = serializers.CharField(allow_null=True)
    country = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    campaign = serializers.UUIDField()
    archived = serializers.BooleanField()


class CardResearchSerializer(serializers.Serializer[Any]):
    researched_at = serializers.DateTimeField()
    industry = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_null=True)
    headquarters = serializers.CharField(allow_null=True)
    employee_count = serializers.IntegerField(allow_null=True)
    business_model = serializers.CharField(allow_null=True)
    classification = serializers.CharField(allow_null=True)
    products_services = serializers.CharField(allow_null=True)
    target_customers = serializers.CharField(allow_null=True)
    sales_headcount = serializers.IntegerField(allow_null=True)
    bd_headcount = serializers.IntegerField(allow_null=True)
    marketing_headcount = serializers.IntegerField(allow_null=True)
    commercial_partnerships_headcount = serializers.IntegerField(allow_null=True)
    headcount_change_3m = serializers.DecimalField(
        max_digits=6, decimal_places=2, allow_null=True, coerce_to_string=False
    )
    headcount_change_6m = serializers.DecimalField(
        max_digits=6, decimal_places=2, allow_null=True, coerce_to_string=False
    )
    headcount_change_12m = serializers.DecimalField(
        max_digits=6, decimal_places=2, allow_null=True, coerce_to_string=False
    )
    source = SourceSerializer()  # type: ignore[assignment]


class CardSignalSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    type = serializers.CharField()
    type_label = serializers.CharField()
    evidence = serializers.CharField()
    event_date = serializers.DateField()
    expires_at = serializers.DateTimeField(allow_null=True)
    source = SourceSerializer()  # type: ignore[assignment]


class CardTriggerSerializer(serializers.Serializer[Any]):
    triggered = serializers.BooleanField()
    signals = CardSignalSerializer(many=True)


class CardIcpSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    fit = serializers.CharField()
    reasons = serializers.ListField(child=serializers.CharField())
    concerns = serializers.ListField(child=serializers.CharField())
    created_at = serializers.DateTimeField()
    profile_version = serializers.IntegerField()


class CardRecommendationSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    explanation = serializers.CharField()
    outreach_angle = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class CardDecisionSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    decision = serializers.CharField()
    note = serializers.CharField(allow_blank=True)
    decided_at = serializers.DateTimeField()
    decided_by = serializers.CharField()
    agrees_with_ai = serializers.BooleanField(
        allow_null=True, help_text="Null when there was no recommendation to compare with."
    )


class CardAnalysisSerializer(serializers.Serializer[Any]):
    job_id = serializers.UUIDField()
    status = serializers.CharField()
    error_summary = serializers.CharField(allow_blank=True)


class ResearchCardSerializer(serializers.Serializer[Any]):
    company = CardCompanySerializer()
    research = CardResearchSerializer(allow_null=True)
    trigger = CardTriggerSerializer()
    icp = CardIcpSerializer(allow_null=True)
    recommendation = CardRecommendationSerializer(allow_null=True)
    decision = CardDecisionSerializer(allow_null=True)
    analysis = CardAnalysisSerializer(allow_null=True)
    gaps = serializers.ListField(child=serializers.CharField())
    outreach_languages = serializers.ListField(child=serializers.CharField())
    outreach_status = serializers.CharField(help_text="none, contacted, replied or meeting_booked.")
    has_angle = serializers.BooleanField(
        help_text="True when an outreach angle exists, so messages can be written."
    )


class DecisionRequestSerializer(serializers.Serializer[Any]):
    decision = serializers.ChoiceField(choices=["add", "hold", "skip"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")


def _source(source: Any) -> dict[str, Any]:
    return {"name": source.name, "url": source.url, "retrieved_at": source.retrieved_at}


def _research(row: CompanyResearch | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "researched_at": row.researched_at,
        "industry": row.industry,
        "description": row.description,
        "headquarters": row.headquarters,
        "employee_count": row.employee_count,
        "business_model": row.business_model,
        "classification": row.classification,
        "products_services": row.products_services,
        "target_customers": row.target_customers,
        "sales_headcount": row.sales_headcount,
        "bd_headcount": row.bd_headcount,
        "marketing_headcount": row.marketing_headcount,
        "commercial_partnerships_headcount": row.commercial_partnerships_headcount,
        "headcount_change_3m": row.headcount_change_3m,
        "headcount_change_6m": row.headcount_change_6m,
        "headcount_change_12m": row.headcount_change_12m,
        "source": _source(row.data_source),
    }


def _signal(signal: Signal) -> dict[str, Any]:
    return {
        "id": signal.pk,
        "type": signal.type,
        "type_label": signal.get_type_display(),
        "evidence": signal.evidence,
        "event_date": signal.event_date,
        "expires_at": signal.expires_at,
        "source": _source(signal.data_source),
    }


def _icp(row: ICPAssessment | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.pk,
        "fit": row.fit,
        "reasons": list(row.reasons),
        "concerns": list(row.concerns),
        "created_at": row.created_at,
        "profile_version": row.campaign_profile.version,
    }


def _recommendation(row: AIRecommendation | None) -> dict[str, Any] | None:
    if row is None:
        return None
    angle = (row.raw_output or {}).get("outreach_angle")
    return {
        "id": row.pk,
        "status": row.status,
        "explanation": row.explanation,
        "outreach_angle": angle if isinstance(angle, str) and angle.strip() else None,
        "created_at": row.created_at,
    }


def _decision(row: HumanDecision | None) -> dict[str, Any] | None:
    if row is None:
        return None
    recommendation = row.ai_recommendation
    return {
        "id": row.pk,
        "decision": row.decision,
        "note": row.note,
        "decided_at": row.decided_at,
        "decided_by": row.decided_by.name or row.decided_by.email,
        "agrees_with_ai": (
            None if recommendation is None else recommendation.status == row.decision
        ),
    }


def _outreach_stage(company: Company) -> str:
    # Imported here: the companies API imports this module.
    from apps.companies.api.pipeline import outreach_stage

    return outreach_stage(company)


def build_card(company: Company) -> dict[str, Any]:
    """The card of ``company``: latest research, trigger state, ICP, recommendation, decision."""
    research = (
        CompanyResearch.objects.filter(company=company)
        .select_related("data_source")
        .latest_first()
        .first()
    )
    assessment = (
        ICPAssessment.objects.filter(company=company)
        .select_related("campaign_profile")
        .latest_first()
        .first()
    )
    recommendation = AIRecommendation.objects.latest_for(company)
    decision = (
        HumanDecision.objects.filter(company=company)
        .select_related("decided_by", "ai_recommendation")
        .latest_first()
        .first()
    )
    state = company_trigger_state(company)
    job = latest_analysis_jobs([company.pk]).get(str(company.pk))
    gaps: list[str] = []
    if research is None or research.sales_headcount is None:
        gaps.append(GAP_HEADCOUNT)
    gaps.append(GAP_BUYERS)
    return {
        "company": {
            "id": company.pk,
            "name": company.name,
            "website": company.website,
            "domain": company.domain,
            "country": company.country,
            "status": company.status,
            "campaign": company.campaign_id,
            "archived": company.is_archived,
        },
        "research": _research(research),
        "trigger": {
            "triggered": state.triggered,
            "signals": [_signal(s) for s in state.signals],
        },
        "icp": _icp(assessment),
        "recommendation": _recommendation(recommendation),
        "decision": _decision(decision),
        "analysis": None
        if job is None
        else {"job_id": job.pk, "status": job.status, "error_summary": job.error_summary},
        "gaps": gaps,
        "outreach_languages": list(company.campaign.current_profile.outreach_languages),
        "has_angle": OutreachAngle.objects.filter(company=company).exists(),
        "outreach_status": _outreach_stage(company),
    }


# ------------------------------------------------------------------ activity and feedback


class ActivityItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    type = serializers.CharField()
    summary = serializers.CharField(help_text="One plain sentence describing what happened.")
    detail = serializers.CharField(allow_blank=True)
    actor = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class FeedbackRequestSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=FeedbackKind.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    target_type = serializers.ChoiceField(choices=FeedbackTargetType.choices, required=False)
    target_id = serializers.UUIDField(required=False)


def describe_activity(activity: Activity) -> tuple[str, str]:
    """(summary, detail) of one timeline entry, from its type and payload."""
    p = activity.payload or {}
    kind = activity.type
    if kind == ActivityType.RESEARCHED:
        pages, signals = p.get("pages"), p.get("signals")
        detail = f"{pages} pages read" if pages else ""
        if signals is not None:
            detail += f"{', ' if detail else ''}{signals} current signal(s)"
        return "Researched the company website", detail
    if kind == ActivityType.RECOMMENDED:
        fit = str(p.get("fit", "")).capitalize()
        rec = str(p.get("recommendation", "")).upper()
        return f"AI judged the fit {fit} and recommended {rec}", ""
    if kind == ActivityType.DECIDED:
        agree = p.get("agrees_with_ai")
        tail = ""
        if agree is not None:
            tail = " (agrees with the AI)" if agree else " (differs from the AI)"
        return f"Decided {str(p.get('decision', '')).upper()}{tail}", str(p.get("note", ""))
    if kind == ActivityType.FEEDBACK:
        raw = str(p.get("kind", ""))
        label = dict(FeedbackKind.choices).get(raw, raw)
        return f"Feedback: {label}", str(p.get("note", ""))
    return activity.get_type_display(), ""


def build_activity(company: Company, limit: int = 50) -> list[dict[str, Any]]:
    """The company's timeline, newest first."""
    items = Activity.objects.timeline(company).select_related("actor")[:limit]
    rows = []
    for item in items:
        summary, detail = describe_activity(item)
        actor = None
        if item.actor is not None:
            actor = item.actor.name or item.actor.email
        rows.append(
            {
                "id": item.pk,
                "type": item.type,
                "summary": summary,
                "detail": detail,
                "actor": actor,
                "created_at": item.created_at,
            }
        )
    return rows
