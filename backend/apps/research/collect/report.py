"""What the research endpoints answer: the runs of a company, its evidence and merged profile
(issues #91, #93 and #100). Plain dicts and serializers; the views only call these."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.core.models import Job, JobItemStatus
from apps.research.collect.run import step_of

STATUS_CHOICES = [s.value for s in JobItemStatus]


class ResearchStepSerializer(serializers.Serializer[Any]):
    step = serializers.ChoiceField(choices=["profile", "signals", "news"])
    status = serializers.ChoiceField(choices=STATUS_CHOICES)
    error = serializers.CharField(allow_blank=True, help_text="Why the step failed, in words.")
    result = serializers.JSONField(
        allow_null=True,
        help_text="What the step found: counts, `missing_fields` for the profile step and a "
        "`skipped_reason` when news was skipped.",
    )
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)


class ResearchRunSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField(
        help_text="`queued`, `running`, `succeeded`, `partial` or `failed`."
    )
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    error_summary = serializers.CharField(allow_blank=True)
    steps = ResearchStepSerializer(many=True)


class ResearchRequestSerializer(serializers.Serializer[Any]):
    """Result of `POST /companies/{id}/research/` (HTTP 202)."""

    company_id = serializers.UUIDField()
    run = ResearchRunSerializer()
    already_queued = serializers.BooleanField(
        help_text="True when the company already had a queued or running run: that run is "
        "returned and nothing new is queued."
    )


class EvidenceBackSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=["research_snapshot", "signal"])
    id = serializers.UUIDField()
    detail = serializers.CharField(help_text="The snapshot date, or the signal type and date.")


class CompanyEvidenceSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    type = serializers.CharField(help_text="`provider`, `website`, `news` or `manual`.")
    name = serializers.CharField(help_text="The provider, site or publication.")
    url = serializers.CharField(allow_blank=True)
    provider_reference = serializers.CharField(allow_blank=True)
    retrieved_at = serializers.DateTimeField()
    evidence_date = serializers.DateField(
        allow_null=True, help_text="The date the source itself states, when it states one."
    )
    age_days = serializers.IntegerField(help_text="Whole days since it was retrieved.")
    backs = EvidenceBackSerializer(many=True)


class ProfileSourceSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    type = serializers.CharField()
    researched_at = serializers.DateTimeField()


class ProfileAlternativeSerializer(serializers.Serializer[Any]):
    value = serializers.CharField()
    origin = ProfileSourceSerializer()


class ProfileFieldSerializer(serializers.Serializer[Any]):
    field = serializers.CharField()
    name = serializers.CharField()
    value = serializers.CharField()
    origin = ProfileSourceSerializer(help_text="Where the shown value comes from.")
    alternatives = ProfileAlternativeSerializer(
        many=True, help_text="Other values found by lower-ranked or older sources."
    )


def run_payload(job: Job, company_id: Any) -> dict[str, Any]:
    """One research run as the API shows it, steps in run order."""
    by_step = {step_of(company_id, item): item for item in job.items.all()}
    steps = []
    for name in ("profile", "signals", "news"):
        item = by_step.get(name)
        if item is None:
            continue
        steps.append(
            {
                "step": name,
                "status": item.status,
                "error": item.error,
                "result": item.result,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
            }
        )
    return {
        "id": job.pk,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_summary": job.error_summary,
        "steps": steps,
    }
