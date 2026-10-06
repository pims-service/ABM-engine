"""ICPAssessment, AIRecommendation and HumanDecision (issue #42).

Design: ``docs/data-model.md``, ADR 0007 (history, AI apart from human), ADR 0008 (fit and
trigger are separate) and ADR 0009 (tenancy, "current" without a flag).

* All three are append-only and carry a direct ``client`` FK copied from the company.
* The AI's answer (``AIRecommendation``) and the person's answer (``HumanDecision``) live in
  different tables. A decision points at the recommendation it answered and never edits it.
* There is no numeric score anywhere. Fit is ``strong`` / ``medium`` / ``weak`` with written
  ``reasons`` and ``concerns`` (Brief section 15).
* Nothing here references signals: a company with a strong fit and no trigger is valid
  (ADR 0008). Whether a trigger is active is computed from signals elsewhere (issue #41).
* "Current" is the latest row per company by timestamp (``latest_for`` / ``current``).

Create rows with ``apps.research.services``; the services also check cross-table consistency
(same client, same company, profile of the company's campaign), which the models re-check on
save.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from apps.campaigns.models import CampaignProfile
from apps.companies.models import Company, CompanyResearch
from apps.core.base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)
from apps.core.fields import StringListField


class Fit(models.TextChoices):
    STRONG = "strong", "Strong"
    MEDIUM = "medium", "Medium"
    WEAK = "weak", "Weak"


class RecommendationStatus(models.TextChoices):
    ADD = "add", "Add"
    HOLD = "hold", "Hold"
    SKIP = "skip", "Skip"


class DecisionChoice(models.TextChoices):
    ADD = "add", "Add"
    HOLD = "hold", "Hold"
    SKIP = "skip", "Skip"


def _in(field_name: str, choices: type[models.TextChoices]) -> Q:
    return Q(**{f"{field_name}__in": list(choices.values)})


# ------------------------------------------------------------------ latest per company


class LatestPerCompanyQuerySet(AppendOnlyQuerySet[Any], TenantQuerySet[Any]):  # type: ignore[override]
    """``latest_for`` / ``current`` over ``latest_order`` (timestamp first, then tie-breaks)."""

    latest_order: ClassVar[tuple[str, ...]] = ("created_at", "id")

    def latest_first(self) -> Any:
        return self.order_by(*(f"-{name}" for name in self.latest_order))

    def latest_for(self, company: Company | Any) -> Any:
        """The current row of one company (a ``Company`` or its id), or ``None``."""
        return self.filter(company=company).latest_first().first()

    def current(self) -> Any:
        """Only each company's latest row, for list and dashboard queries."""
        outer = models.OuterRef
        newer_q = Q()
        for i, name in enumerate(self.latest_order):
            clause = Q(**{f"{name}__gt": outer(name)})
            for earlier in self.latest_order[:i]:
                clause &= Q(**{earlier: outer(earlier)})
            newer_q |= clause
        newer = self.model._default_manager.filter(company=outer("company")).filter(newer_q)
        return self.filter(~models.Exists(newer))


# ------------------------------------------------------------------ ICPAssessment


class ICPAssessmentQuerySet(LatestPerCompanyQuerySet):  # type: ignore[override]
    latest_order = ("created_at", "id")


class ICPAssessment(AppendOnlyModel, TenantModel, UUIDModel):
    """ICP Fit of one company against one version of the campaign rules. Fit only.

    No trigger input and no score. It records both the rules used (``campaign_profile``) and the
    facts judged (``company_research``). ``raw_output`` is the validated structured output of
    the model call, kept for debugging and prompt work.
    """

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="icp_assessments")
    campaign_profile = models.ForeignKey(
        CampaignProfile, on_delete=models.PROTECT, related_name="icp_assessments"
    )
    company_research = models.ForeignKey(
        CompanyResearch, on_delete=models.PROTECT, related_name="icp_assessments"
    )
    fit = models.CharField(max_length=8, choices=Fit.choices)
    reasons = StringListField(help_text="Why this fit, in plain words.")
    concerns = StringListField(help_text="Doubts or risks. May be empty.")
    raw_output = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=50)
    schema_version = models.CharField(max_length=50)
    created_at = models.DateTimeField(default=timezone.now)

    objects = ICPAssessmentQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["company", "-created_at", "-id"], name="rs_icp_latest_idx"),
            models.Index(fields=["client"], name="rs_icp_client_idx"),
            models.Index(fields=["campaign_profile"], name="rs_icp_profile_idx"),
            models.Index(fields=["company_research"], name="rs_icp_research_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("fit", Fit), name="research_icpassessment_fit_valid"
            ),
            models.CheckConstraint(
                condition=~Q(model_name="") & ~Q(prompt_version="") & ~Q(schema_version=""),
                name="research_icpassessment_versions_set",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}: {self.fit}"

    def sync_client(self) -> None:
        super().sync_client()
        if self.campaign_profile.client_id != self.client_id:
            raise TenantMismatchError(
                "ICPAssessment.campaign_profile belongs to a different client."
            )
        if self.campaign_profile.campaign_id != self.company.campaign_id:
            raise TenantMismatchError(
                "ICPAssessment.campaign_profile belongs to a different campaign than the company."
            )
        if self.company_research.company_id != self.company_id:
            raise TenantMismatchError(
                "ICPAssessment.company_research belongs to a different company."
            )


# ------------------------------------------------------------------ AIRecommendation


class AIRecommendationQuerySet(LatestPerCompanyQuerySet):  # type: ignore[override]
    latest_order = ("created_at", "id")


class AIRecommendation(AppendOnlyModel, TenantModel, UUIDModel):
    """What the AI suggests doing (add, hold or skip) and why (Brief sections 11 and 15).

    Separate table from ``HumanDecision``: a person's call never edits this row.
    """

    tenant_parent = "company"

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="ai_recommendations"
    )
    icp_assessment = models.ForeignKey(
        ICPAssessment, on_delete=models.PROTECT, related_name="recommendations"
    )
    status = models.CharField(max_length=8, choices=RecommendationStatus.choices)
    explanation = models.TextField()
    raw_output = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=50)
    schema_version = models.CharField(max_length=50)
    created_at = models.DateTimeField(default=timezone.now)

    objects = AIRecommendationQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["company", "-created_at", "-id"], name="rs_airec_latest_idx"),
            models.Index(fields=["client"], name="rs_airec_client_idx"),
            models.Index(fields=["icp_assessment"], name="rs_airec_assessment_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("status", RecommendationStatus),
                name="research_airecommendation_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(explanation=""), name="research_airecommendation_explained"
            ),
            models.CheckConstraint(
                condition=~Q(model_name="") & ~Q(prompt_version="") & ~Q(schema_version=""),
                name="research_airecommendation_versions_set",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}: {self.status}"

    def sync_client(self) -> None:
        super().sync_client()
        if self.icp_assessment.company_id != self.company_id:
            raise TenantMismatchError(
                "AIRecommendation.icp_assessment belongs to a different company."
            )


# ------------------------------------------------------------------ HumanDecision


@dataclass
class AgreementSummary:
    """AI versus human agreement (Brief section 11).

    ``compared`` counts decisions made against an AI recommendation. ``without_ai`` counts
    decisions with no recommendation; they are left out of the rate. ``pairs`` maps
    ``(ai_status, human_decision)`` to a count.
    """

    compared: int = 0
    agreed: int = 0
    without_ai: int = 0
    pairs: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def disagreed(self) -> int:
        return self.compared - self.agreed

    @property
    def rate(self) -> float | None:
        """Share of compared decisions that matched the AI, or ``None`` when nothing compared."""
        return self.agreed / self.compared if self.compared else None


class HumanDecisionQuerySet(LatestPerCompanyQuerySet):  # type: ignore[override]
    latest_order = ("decided_at", "created_at", "id")

    def with_ai_status(self) -> Any:
        """Annotate ``ai_status`` (the recommendation answered, or null)."""
        return self.annotate(ai_status=models.F("ai_recommendation__status"))

    def compared(self) -> Any:
        """Decisions made against an AI recommendation."""
        return self.filter(ai_recommendation__isnull=False)

    def agreeing(self) -> Any:
        return self.compared().filter(decision=models.F("ai_recommendation__status"))

    def overrides(self) -> Any:
        """Decisions where the person chose something other than the AI recommended."""
        return self.compared().exclude(decision=models.F("ai_recommendation__status"))

    def agreement(self) -> AgreementSummary:
        """How often the human matched the AI. Chain ``.current()`` to use only each company's
        latest decision, or ``.for_client(client)`` to scope it."""
        summary = AgreementSummary(without_ai=self.filter(ai_recommendation__isnull=True).count())
        rows = (
            self.compared()
            .order_by()
            .values_list("ai_recommendation__status", "decision")
            .annotate(n=Count("id"))
        )
        for ai_status, human, n in rows:
            summary.pairs[(ai_status, human)] = n
            summary.compared += n
            if ai_status == human:
                summary.agreed += n
        return summary


class HumanDecision(AppendOnlyModel, TenantModel, UUIDModel):
    """The person's call on a company. Never edited: changing one's mind adds a new row.

    ``ai_recommendation`` is the recommendation the person answered. It is nullable on purpose:
    a person may decide before the AI has run, or on a company that was never assessed (for
    example a manual add). Such decisions are valid and are excluded from agreement figures.
    ``decided_by`` is always a real user.
    """

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="human_decisions")
    ai_recommendation = models.ForeignKey(
        AIRecommendation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="human_decisions",
    )
    decision = models.CharField(max_length=8, choices=DecisionChoice.choices)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    note = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HumanDecisionQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-decided_at", "-created_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["company", "-decided_at", "-created_at", "-id"],
                name="rs_decision_latest_idx",
            ),
            models.Index(fields=["client"], name="rs_decision_client_idx"),
            models.Index(fields=["ai_recommendation"], name="rs_decision_airec_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("decision", DecisionChoice),
                name="research_humandecision_decision_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}: {self.decision}"

    def sync_client(self) -> None:
        super().sync_client()
        rec = self.ai_recommendation
        if rec is not None and rec.company_id != self.company_id:
            raise TenantMismatchError(
                "HumanDecision.ai_recommendation belongs to a different company."
            )
