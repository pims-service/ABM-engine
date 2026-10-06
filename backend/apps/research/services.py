"""Write path for ICP assessments, AI recommendations and human decisions (issue #42).

Everything here appends a row. Nothing edits or deletes an earlier one, and recording a human
decision never touches the AI recommendation it answers (ADR 0007). Permission checks belong
to the API layer; these functions enforce data rules.

* ``record_icp_assessment``: fit (strong, medium, weak) with reasons and concerns, against a
  campaign profile version and a research snapshot of the same company.
* ``record_ai_recommendation``: add, hold or skip, with an explanation, for one assessment.
* ``record_human_decision``: add, hold or skip by a user, optionally answering a recommendation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.campaigns.models import CampaignProfile
from apps.companies.models import Company, CompanyResearch
from apps.core.base import TenantMismatchError

from .models import (
    AIRecommendation,
    DecisionChoice,
    Fit,
    HumanDecision,
    ICPAssessment,
    RecommendationStatus,
)

__all__ = ["record_ai_recommendation", "record_human_decision", "record_icp_assessment"]


def _choice(value: str, choices: Any) -> str:
    if value not in choices.values:
        raise ValidationError(f"'{value}' is not one of: {', '.join(choices.values)}.")
    return value


def _clean_list(items: Iterable[str], name: str, *, required: bool) -> list[str]:
    if isinstance(items, str):
        raise ValidationError({name: "Give a list of strings, not one string."})
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    if required and not cleaned:
        raise ValidationError({name: f"At least one entry is required in {name}."})
    return cleaned


def _reject_scores(value: Any, path: str = "raw_output") -> None:
    """The output must not carry a numeric score (Brief section 15): explain, do not rank."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if "score" in str(key).lower():
                raise ValidationError({"raw_output": f"{path}.{key}: scores are not allowed."})
            _reject_scores(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_scores(item, path)


def _model_fields(
    raw_output: Mapping[str, Any] | None, model_name: str, prompt_version: str, schema_version: str
) -> dict[str, Any]:
    raw = dict(raw_output or {})
    _reject_scores(raw)
    for name, value in (
        ("model_name", model_name),
        ("prompt_version", prompt_version),
        ("schema_version", schema_version),
    ):
        if not value.strip():
            raise ValidationError({name: f"{name} is required."})
    return {
        "raw_output": raw,
        "model_name": model_name.strip(),
        "prompt_version": prompt_version.strip(),
        "schema_version": schema_version.strip(),
    }


def record_icp_assessment(
    company: Company,
    fit: str,
    reasons: Iterable[str],
    concerns: Iterable[str] = (),
    *,
    model_name: str,
    prompt_version: str,
    schema_version: str,
    raw_output: Mapping[str, Any] | None = None,
    campaign_profile: CampaignProfile | None = None,
    company_research: CompanyResearch | None = None,
    created_at: datetime | None = None,
) -> ICPAssessment:
    """Append an ICP assessment (fit only: no trigger input, no score).

    ``campaign_profile`` defaults to the campaign's current profile version and
    ``company_research`` to the company's latest snapshot; either must belong to the company
    (same client and campaign, same company). A company with no research cannot be assessed.
    """
    fit = _choice(fit, Fit)
    reason_list = _clean_list(reasons, "reasons", required=True)
    concern_list = _clean_list(concerns, "concerns", required=False)
    extra = _model_fields(raw_output, model_name, prompt_version, schema_version)
    if campaign_profile is None:
        campaign_profile = company.campaign.current_profile
    if company_research is None:
        company_research = CompanyResearch.objects.latest_for(company)
        if company_research is None:
            raise ValidationError("Research this company before assessing its fit.")
    created = {} if created_at is None else {"created_at": created_at}
    # ``save`` re-checks client, campaign and company consistency (TenantMismatchError).
    assessment = ICPAssessment(
        company=company,
        campaign_profile=campaign_profile,
        company_research=company_research,
        fit=fit,
        reasons=reason_list,
        concerns=concern_list,
        **extra,
        **created,
    )
    assessment.save()
    return assessment


def record_ai_recommendation(
    icp_assessment: ICPAssessment,
    status: str,
    explanation: str,
    *,
    model_name: str,
    prompt_version: str,
    schema_version: str,
    raw_output: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
) -> AIRecommendation:
    """Append the AI's recommendation (add, hold, skip) for one assessment, with its reasons.

    The company comes from the assessment. The recommendation may weigh current signals, but
    its eligibility never requires a trigger (ADR 0008).
    """
    status = _choice(status, RecommendationStatus)
    if not explanation.strip():
        raise ValidationError({"explanation": "An explanation is required (Brief section 15)."})
    extra = _model_fields(raw_output, model_name, prompt_version, schema_version)
    created = {} if created_at is None else {"created_at": created_at}
    rec = AIRecommendation(
        company=icp_assessment.company,
        icp_assessment=icp_assessment,
        status=status,
        explanation=explanation.strip(),
        **extra,
        **created,
    )
    rec.save()
    return rec


@transaction.atomic
def record_human_decision(
    company: Company,
    decision: str,
    user: User,
    *,
    ai_recommendation: AIRecommendation | None = None,
    note: str = "",
    decided_at: datetime | None = None,
) -> HumanDecision:
    """Append a person's decision (add, hold, skip). Never edits the AI recommendation.

    ``ai_recommendation`` is the one being answered (it must be for the same company), or
    ``None`` when the person decides with no recommendation. ``decided_by`` is the user.
    """
    decision = _choice(decision, DecisionChoice)
    if user is None or not getattr(user, "pk", None):
        raise ValidationError({"decided_by": "A decision needs the person who made it."})
    if ai_recommendation is not None and ai_recommendation.company_id != company.pk:
        raise TenantMismatchError("The AI recommendation is for a different company.")
    extra = {} if decided_at is None else {"decided_at": decided_at}
    record = HumanDecision(
        company=company,
        ai_recommendation=ai_recommendation,
        decision=decision,
        decided_by=user,
        note=note.strip(),
        **extra,
    )
    record.save()
    return record
