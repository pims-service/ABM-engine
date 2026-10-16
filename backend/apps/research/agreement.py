"""How often people agree with the AI, and what they flag (Brief sections 11 and 16).

Deliberately small (Brief section 21): counts and one CSV, no analytics suite.

* Agreement uses each company's *latest* human decision whose date falls in the range, against the
  AI recommendation it answered. Decisions taken with no recommendation are counted apart and left
  out of the rate.
* Feedback counts are the flags people raised (Activity type ``feedback``) in the range.
* The feedback CSV carries the assessment text and the prompt version of the latest AI run for the
  company, so a flag can be traced to the prompt that produced the judgement.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from apps.companies.api.pipeline import safe
from apps.outreach.models import Activity
from apps.research.models import AIRecommendation, HumanDecision, ICPAssessment

DECISIONS = ("add", "hold", "skip")


def bounds(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive dates -> a half-open datetime range in the current time zone."""
    zone = timezone.get_current_timezone()
    low = timezone.make_aware(datetime.combine(start, time.min), zone) if start else None
    high = (
        timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), zone)
        if end
        else None
    )
    return low, high


def _in_range(queryset: Any, field: str, low: datetime | None, high: datetime | None) -> Any:
    if low:
        queryset = queryset.filter(**{f"{field}__gte": low})
    if high:
        queryset = queryset.filter(**{f"{field}__lt": high})
    return queryset


def agreement_report(
    campaign_id: Any, low: datetime | None, high: datetime | None
) -> dict[str, Any]:
    decisions = HumanDecision.objects.filter(company__campaign_id=campaign_id).current()
    decisions = _in_range(decisions, "decided_at", low, high)
    summary = decisions.agreement()
    matrix = [
        {
            "ai": ai,
            "human": human,
            "count": summary.pairs.get((ai, human), 0),
        }
        for ai in DECISIONS
        for human in DECISIONS
    ]
    flags = _in_range(
        Activity.objects.feedback().filter(campaign_id=campaign_id), "created_at", low, high
    )
    counts = Counter(str(p.get("kind", "other")) for p in flags.values_list("payload", flat=True))
    return {
        "compared": summary.compared,
        "agreed": summary.agreed,
        "disagreed": summary.disagreed,
        "without_ai": summary.without_ai,
        "rate": summary.rate,
        "matrix": matrix,
        "feedback": [{"kind": k, "count": n} for k, n in counts.most_common()],
    }


CSV_HEADER = (
    "Date",
    "Company",
    "Website",
    "Flag",
    "Note",
    "Flagged by",
    "ICP fit",
    "ICP reasons",
    "ICP concerns",
    "AI recommendation",
    "AI explanation",
    "Prompt version",
    "Model",
)


def feedback_csv(campaign_id: Any, low: datetime | None, high: datetime | None) -> str:
    flags = list(
        _in_range(
            Activity.objects.feedback().filter(campaign_id=campaign_id),
            "created_at",
            low,
            high,
        )
        .select_related("company", "actor")
        .order_by("-created_at", "-id")[:5000]
    )
    company_ids = {f.company_id for f in flags}
    assessments: dict[Any, ICPAssessment] = {}
    for row in ICPAssessment.objects.filter(company_id__in=company_ids).current():
        assessments[row.company_id] = row
    recommendations: dict[Any, AIRecommendation] = {}
    for rec in AIRecommendation.objects.filter(company_id__in=company_ids).current():
        recommendations[rec.company_id] = rec
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for flag in flags:
        icp = assessments.get(flag.company_id)
        rec = recommendations.get(flag.company_id)
        payload = flag.payload or {}
        writer.writerow(
            [
                safe(flag.created_at.date().isoformat()),
                safe(flag.company.name),
                safe(flag.company.website),
                safe(payload.get("kind", "")),
                safe(payload.get("note", "")),
                safe(flag.actor.email if flag.actor else ""),
                safe(icp.fit if icp else ""),
                safe("; ".join(icp.reasons) if icp else ""),
                safe("; ".join(icp.concerns) if icp else ""),
                safe(rec.status if rec else ""),
                safe(rec.explanation if rec else ""),
                safe(rec.prompt_version if rec else ""),
                safe(rec.model_name if rec else ""),
            ]
        )
    return out.getvalue()


class AgreementQuerySerializer(serializers.Serializer[Any]):
    start = serializers.DateField(required=False, help_text="First day (inclusive).")
    end = serializers.DateField(required=False, help_text="Last day (inclusive).")


class MatrixCellSerializer(serializers.Serializer[Any]):
    ai = serializers.CharField()
    human = serializers.CharField()
    count = serializers.IntegerField()


class FeedbackCountSerializer(serializers.Serializer[Any]):
    kind = serializers.CharField()
    count = serializers.IntegerField()


class AgreementReportSerializer(serializers.Serializer[Any]):
    compared = serializers.IntegerField(help_text="Decisions made against an AI recommendation.")
    agreed = serializers.IntegerField()
    disagreed = serializers.IntegerField()
    without_ai = serializers.IntegerField(
        help_text="Decisions with no recommendation; not in rate."
    )
    rate = serializers.FloatField(allow_null=True, help_text="agreed / compared, null if none.")
    matrix = MatrixCellSerializer(many=True)
    feedback = FeedbackCountSerializer(many=True)
