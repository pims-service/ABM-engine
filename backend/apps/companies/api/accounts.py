"""The master ABM dashboard of a campaign (Brief section 13).

``GET /campaigns/{id}/accounts/`` lists the campaign's companies with what the research found
(ICP fit, trigger, AI recommendation, human status, industry, size, primary buyer), filterable
and sortable. ``GET .../accounts/summary/`` gives the counts for the header, and
``POST .../accounts/analyze/`` queues the analysis of many companies at once.

Read only except for the bulk queue: every value comes from the stored records of the research
engine. Nothing is computed with a model here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, ClassVar

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies.analysis import latest_analysis_jobs, request_analysis
from apps.companies.models import Company, CompanyResearch, CompanyStatus, Contact
from apps.companies.tasks import ANALYSIS_JOB_TYPE
from apps.core.jobs import reap_stale_jobs
from apps.core.models import Job, JobItem, JobStatus
from apps.core.permissions import ClientScopedViewSet, request_roles
from apps.core.roles import Level, role_allows
from apps.core.schema import error_responses
from apps.core.tenancy import is_global_admin
from apps.outreach.messages_api import BulkCrmResultSerializer, bulk_push
from apps.outreach.models import Activity, ActivityType
from apps.outreach.services import record_activity
from apps.research.models import (
    AIRecommendation,
    Fit,
    HumanDecision,
    ICPAssessment,
    RecommendationStatus,
    Signal,
    SignalType,
)
from apps.research.services import record_human_decision

from .pipeline import approved_accounts_csv, stages_for

TAGS = ["campaigns"]
MAX_BULK = 500
_FIT_RANK = {Fit.STRONG: 3, Fit.MEDIUM: 2, Fit.WEAK: 1}


# ------------------------------------------------------------------ serializers


class AccountAnalysisSerializer(serializers.Serializer[Any]):
    status = serializers.CharField()
    error_summary = serializers.CharField(allow_blank=True)


class AccountRowSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    website = serializers.CharField(allow_blank=True)
    domain = serializers.CharField(allow_null=True)
    country = serializers.CharField(allow_blank=True)
    status = serializers.CharField(help_text="Analysis status of the company.")
    industry = serializers.CharField(allow_null=True)
    employee_count = serializers.IntegerField(allow_null=True)
    icp_fit = serializers.CharField(allow_null=True, help_text="strong, medium, weak or null.")
    trigger = serializers.BooleanField(help_text="True when at least one fresh signal exists.")
    trigger_types = serializers.ListField(child=serializers.CharField())
    ai_recommendation = serializers.CharField(allow_null=True)
    human_decision = serializers.CharField(allow_null=True)
    review = serializers.CharField(
        help_text="`not_assessed`, `needs_review` (assessed, no human decision) or `decided`."
    )
    primary_buyer = serializers.CharField(allow_null=True)
    outreach_status = serializers.CharField(
        help_text="none, contacted, replied or meeting_booked (the furthest step reached)."
    )
    analysis = AccountAnalysisSerializer(allow_null=True)


class AccountQuerySerializer(serializers.Serializer[dict[str, str]]):
    """Query parameters of the master list. `search` and `ordering` are the DRF ones."""

    icp_fit = serializers.ChoiceField(
        choices=["strong", "medium", "weak", "unassessed"], required=False
    )
    trigger = serializers.ChoiceField(choices=["yes", "no"], required=False)
    trigger_type = serializers.ChoiceField(choices=SignalType.choices, required=False)
    ai = serializers.ChoiceField(choices=RecommendationStatus.choices, required=False)
    human = serializers.ChoiceField(choices=["add", "hold", "skip", "undecided"], required=False)
    review = serializers.ChoiceField(choices=["needs_review", "decided"], required=False)
    industry = serializers.CharField(required=False, max_length=200)
    country = serializers.CharField(required=False, max_length=2)
    size_min = serializers.IntegerField(required=False, min_value=0)
    size_max = serializers.IntegerField(required=False, min_value=0)
    buyer_title = serializers.CharField(required=False, max_length=200)
    outreach = serializers.ChoiceField(
        choices=["none", "contacted", "replied", "meeting_booked"], required=False
    )
    archived = serializers.ChoiceField(choices=["false", "true", "all"], required=False)


class AccountSummarySerializer(serializers.Serializer[Any]):
    total = serializers.IntegerField()
    not_assessed = serializers.IntegerField()
    analyzing = serializers.IntegerField()
    failed = serializers.IntegerField()
    strong = serializers.IntegerField()
    medium = serializers.IntegerField()
    weak = serializers.IntegerField()
    trigger_yes = serializers.IntegerField()
    needs_review = serializers.IntegerField()
    human_add = serializers.IntegerField()
    human_hold = serializers.IntegerField()
    human_skip = serializers.IntegerField()
    contacted = serializers.IntegerField(help_text="Contacted, whatever happened after.")
    replied = serializers.IntegerField(help_text="Replied or booked a meeting.")
    meetings = serializers.IntegerField()


class BulkAnalyzeRequestSerializer(serializers.Serializer[Any]):
    scope = serializers.ChoiceField(
        choices=["unassessed", "failed", "ids"],
        help_text="`unassessed`: companies never analyzed; `failed`: those whose last analysis "
        "failed; `ids`: the companies in `company_ids`.",
    )
    company_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=MAX_BULK
    )


MAX_BULK_DECISIONS = 200


class BulkDecisionRequestSerializer(serializers.Serializer[Any]):
    company_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=MAX_BULK_DECISIONS
    )
    decision = serializers.ChoiceField(choices=["add", "hold", "skip"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")


class BulkDecisionItemSerializer(serializers.Serializer[Any]):
    company_id = serializers.UUIDField()
    result = serializers.ChoiceField(choices=["saved", "unchanged", "error"])
    error = serializers.CharField(allow_blank=True)


class BulkDecisionResultSerializer(serializers.Serializer[Any]):
    saved = serializers.IntegerField()
    unchanged = serializers.IntegerField()
    failed = serializers.IntegerField()
    results = BulkDecisionItemSerializer(many=True)


class BulkAnalyzeResultSerializer(serializers.Serializer[Any]):
    requested = serializers.IntegerField()
    queued = serializers.IntegerField()
    already_running = serializers.IntegerField()


# ------------------------------------------------------------------ query building


def _latest(model: Any, **extra: Any) -> Any:
    """Exists: the company's current (latest) row of ``model`` matches ``extra``."""
    return Exists(model.objects.current().filter(company=OuterRef("pk"), **extra))


def _busy_company_ids(ids: list[Any]) -> set[Any]:
    """Companies with an analysis already queued or running, in ONE query."""
    return set(
        JobItem.objects.filter(
            subject_type="company",
            subject_id__in=ids,
            job__type=ANALYSIS_JOB_TYPE,
            job__status__in=[JobStatus.QUEUED, JobStatus.RUNNING],
        ).values_list("subject_id", flat=True)
    )


def _reached(*types: str) -> Any:
    return Exists(Activity.objects.filter(company=OuterRef("pk"), type__in=types))


def _fresh_signals(**extra: Any) -> Any:
    return Exists(Signal.objects.fresh().filter(company=OuterRef("pk"), **extra))


def _fit_rank_subquery() -> Any:
    latest = ICPAssessment.objects.current().filter(company=OuterRef("pk"))
    return Coalesce(
        Subquery(
            latest.annotate(
                rank=Case(
                    *[When(fit=fit, then=Value(rank)) for fit, rank in _FIT_RANK.items()],
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ).values("rank")[:1]
        ),
        Value(0),
    )


def filter_accounts(queryset: QuerySet[Company], params: dict[str, Any]) -> QuerySet[Company]:
    """Apply the validated query parameters (all filters run in the database)."""
    if (fit := params.get("icp_fit")) == "unassessed":
        queryset = queryset.filter(~_latest(ICPAssessment))
    elif fit:
        queryset = queryset.filter(_latest(ICPAssessment, fit=fit))
    if (trigger := params.get("trigger")) == "yes":
        queryset = queryset.filter(_fresh_signals())
    elif trigger == "no":
        queryset = queryset.filter(~_fresh_signals())
    if kind := params.get("trigger_type"):
        queryset = queryset.filter(_fresh_signals(type=kind))
    if ai := params.get("ai"):
        queryset = queryset.filter(_latest(AIRecommendation, status=ai))
    if (human := params.get("human")) == "undecided":
        queryset = queryset.filter(~_latest(HumanDecision))
    elif human:
        queryset = queryset.filter(_latest(HumanDecision, decision=human))
    if (review := params.get("review")) == "needs_review":
        queryset = queryset.filter(_latest(ICPAssessment), ~_latest(HumanDecision))
    elif review == "decided":
        queryset = queryset.filter(_latest(HumanDecision))
    research: dict[str, Any] = {}
    if industry := params.get("industry"):
        research["industry__icontains"] = industry
    if (stage := params.get("outreach")) == "none":
        queryset = queryset.filter(
            ~_reached(ActivityType.CONTACTED, ActivityType.REPLIED, ActivityType.MEETING_BOOKED)
        )
    elif stage == "contacted":
        queryset = queryset.filter(
            _reached(ActivityType.CONTACTED, ActivityType.REPLIED, ActivityType.MEETING_BOOKED),
            ~_reached(ActivityType.REPLIED, ActivityType.MEETING_BOOKED),
        )
    elif stage == "replied":
        queryset = queryset.filter(
            _reached(ActivityType.REPLIED), ~_reached(ActivityType.MEETING_BOOKED)
        )
    elif stage == "meeting_booked":
        queryset = queryset.filter(_reached(ActivityType.MEETING_BOOKED))
    if (low := params.get("size_min")) is not None:
        research["employee_count__gte"] = low
    if (high := params.get("size_max")) is not None:
        research["employee_count__lte"] = high
    if research:
        queryset = queryset.filter(
            Exists(CompanyResearch.objects.current().filter(company=OuterRef("pk"), **research))
        )
    if country := params.get("country"):
        queryset = queryset.filter(country__iexact=country)
    if title := params.get("buyer_title"):
        queryset = queryset.filter(
            Exists(Contact.objects.active().filter(company=OuterRef("pk"), title__icontains=title))
        )
    return queryset


def build_rows(companies: list[Company]) -> list[dict[str, Any]]:
    """One row per company, from a handful of batched queries (no query per row)."""
    ids = [c.pk for c in companies]
    research = {r.company_id: r for r in CompanyResearch.objects.current().filter(company__in=ids)}
    fits = {a.company_id: a for a in ICPAssessment.objects.current().filter(company__in=ids)}
    recs = {r.company_id: r for r in AIRecommendation.objects.current().filter(company__in=ids)}
    decisions = {d.company_id: d for d in HumanDecision.objects.current().filter(company__in=ids)}
    types: dict[Any, list[str]] = defaultdict(list)
    for signal in Signal.objects.fresh().filter(company__in=ids).order_by("-event_date"):
        if signal.get_type_display() not in types[signal.company_id]:
            types[signal.company_id].append(signal.get_type_display())
    buyers = {
        c.company_id: c for c in Contact.objects.active().filter(company__in=ids, role="primary")
    }
    jobs = latest_analysis_jobs(ids)
    stages = stages_for(ids)
    rows = []
    for company in companies:
        snap = research.get(company.pk)
        assessment = fits.get(company.pk)
        decision = decisions.get(company.pk)
        buyer = buyers.get(company.pk)
        job = jobs.get(str(company.pk))
        review = "not_assessed" if assessment is None else "decided" if decision else "needs_review"
        rows.append(
            {
                "id": company.pk,
                "name": company.name,
                "website": company.website,
                "domain": company.domain,
                "country": company.country,
                "status": company.status,
                "industry": snap.industry if snap else None,
                "employee_count": snap.employee_count if snap else None,
                "icp_fit": assessment.fit if assessment else None,
                "trigger": bool(types.get(company.pk)),
                "trigger_types": types.get(company.pk, []),
                "ai_recommendation": recs[company.pk].status if company.pk in recs else None,
                "human_decision": decision.decision if decision else None,
                "review": review,
                "outreach_status": stages.get(company.pk, "none"),
                "primary_buyer": (
                    f"{buyer.name}, {buyer.title}" if buyer and buyer.title else buyer.name
                )
                if buyer
                else None,
                "analysis": None
                if job is None
                else {"status": job.status, "error_summary": job.error_summary},
            }
        )
    return rows


# ------------------------------------------------------------------ the view


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="campaign_accounts_list",
        summary="Master ABM dashboard rows",
        description="The campaign's companies with ICP fit, trigger, AI recommendation, human "
        "status, industry, size and primary buyer (Brief section 13). Filter by ICP fit "
        "(`unassessed` too), trigger yes or no and type, AI recommendation, human decision, "
        "review state, industry, country, size and buyer title. `search` matches name, domain "
        "and website; `ordering` accepts `name`, `icp_fit`, `updated_at` (prefix `-` to "
        "reverse). Archived companies are hidden unless `archived` asks. Needs the READ level.",
        parameters=[AccountQuerySerializer],
        responses={
            200: AccountRowSerializer(many=True),
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    ),
)
class AccountViewSet(mixins.ListModelMixin, ClientScopedViewSet):
    """``/campaigns/{campaign_pk}/accounts/`` (list), ``summary`` and ``analyze``."""

    queryset = Company.objects.all()
    serializer_class = AccountRowSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name", "domain", "website")
    ordering_fields = ("name", "updated_at", "icp_fit")
    ordering = ("name", "id")

    action_levels: ClassVar[dict[str, Level]] = {
        "list": Level.READ,
        "summary": Level.READ,
        "analyze": Level.EDIT,
        "export": Level.EDIT,
        "push_to_crm": Level.EDIT,
        "decide": Level.DECIDE,
    }

    def _campaign(self, level: Level) -> Campaign:
        try:
            campaign = (
                Campaign.objects.for_user(self.request.user)
                .select_related("client")
                .get(pk=self.kwargs["campaign_pk"])
            )
        except Campaign.DoesNotExist as exc:
            raise Http404 from exc
        if not is_global_admin(self.request.user):
            role = request_roles(self.request).get(campaign.client_id)
            if not role_allows(role, level):
                raise PermissionDenied("Your role does not allow this action.")
        return campaign

    def _campaign_companies(self, level: Level, archived: str = "false") -> Any:
        campaign = self._campaign(level)
        queryset = super().get_queryset().filter(campaign_id=campaign.pk)
        if archived == "false":
            return queryset.active()
        if archived == "true":
            return queryset.archived()
        return queryset

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return super().get_queryset()
        query = AccountQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        queryset = self._campaign_companies(Level.READ, params.get("archived", "false"))
        queryset = filter_accounts(queryset, params)
        ordering = self.request.query_params.get("ordering", "")
        if ordering.lstrip("-") == "icp_fit":
            queryset = queryset.annotate(icp_fit=_fit_rank_subquery())
        return queryset

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        rows = build_rows(list(page) if page is not None else [])
        return self.get_paginated_response(AccountRowSerializer(rows, many=True).data)

    @extend_schema(
        tags=TAGS,
        operation_id="campaign_accounts_summary",
        summary="Counts for the dashboard header",
        description="Counts over the campaign's active companies, ignoring any filter. Needs the "
        "READ level.",
        responses={200: AccountSummarySerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request, campaign_pk: str | None = None) -> Response:
        companies = self._campaign_companies(Level.READ)
        reap_stale_jobs(campaign_id=self.kwargs["campaign_pk"])
        analyzing = Job.objects.filter(
            campaign_id=self.kwargs["campaign_pk"],
            type=ANALYSIS_JOB_TYPE,
            status__in=[JobStatus.QUEUED, JobStatus.RUNNING],
        ).count()
        assessed = Q(_latest(ICPAssessment))
        # One scan of the companies instead of fifteen counts (the page polls this).
        body = companies.aggregate(
            total=Count("pk"),
            not_assessed=Count("pk", filter=~assessed),
            failed=Count("pk", filter=Q(status=CompanyStatus.FAILED)),
            strong=Count("pk", filter=Q(_latest(ICPAssessment, fit="strong"))),
            medium=Count("pk", filter=Q(_latest(ICPAssessment, fit="medium"))),
            weak=Count("pk", filter=Q(_latest(ICPAssessment, fit="weak"))),
            trigger_yes=Count("pk", filter=Q(_fresh_signals())),
            needs_review=Count("pk", filter=assessed & ~Q(_latest(HumanDecision))),
            human_add=Count("pk", filter=Q(_latest(HumanDecision, decision="add"))),
            human_hold=Count("pk", filter=Q(_latest(HumanDecision, decision="hold"))),
            human_skip=Count("pk", filter=Q(_latest(HumanDecision, decision="skip"))),
            contacted=Count(
                "pk",
                filter=Q(
                    _reached(
                        ActivityType.CONTACTED, ActivityType.REPLIED, ActivityType.MEETING_BOOKED
                    )
                ),
            ),
            replied=Count(
                "pk",
                filter=Q(_reached(ActivityType.REPLIED, ActivityType.MEETING_BOOKED)),
            ),
            meetings=Count("pk", filter=Q(_reached(ActivityType.MEETING_BOOKED))),
        )
        body["analyzing"] = analyzing
        return Response(AccountSummarySerializer(body).data)

    @extend_schema(
        tags=TAGS,
        operation_id="campaign_accounts_analyze",
        summary="Analyze many companies",
        description=f"Queues the analysis of up to {MAX_BULK} companies, one background job each "
        "(the same as Analyze Company on each). Companies that already have an analysis "
        "queued or running are counted in `already_running` and not queued again. Each analysis "
        "calls the AI provider, which costs money. Needs the EDIT level.",
        request=BulkAnalyzeRequestSerializer,
        responses={
            202: BulkAnalyzeResultSerializer,
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    @action(detail=False, methods=["post"])
    def analyze(self, request: Request, campaign_pk: str | None = None) -> Response:
        body = BulkAnalyzeRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        scope = body.validated_data["scope"]
        companies = self._campaign_companies(Level.EDIT)
        if scope == "unassessed":
            companies = companies.filter(~_latest(ICPAssessment))
        elif scope == "failed":
            companies = companies.filter(status=CompanyStatus.FAILED)
        else:
            ids = body.validated_data.get("company_ids") or []
            if not ids:
                raise serializers.ValidationError({"company_ids": ["Send at least one id."]})
            companies = companies.filter(pk__in=ids)
        user = request.user if isinstance(request.user, User) else None
        queued = running = 0
        reap_stale_jobs(campaign_id=self.kwargs["campaign_pk"])
        picked = list(companies.order_by("name", "id")[:MAX_BULK])
        busy = _busy_company_ids([c.pk for c in picked])
        for company in picked:
            if company.pk in busy:  # already queued or running: no lock, no write
                running += 1
                continue
            result = request_analysis(company, user)
            if result.created:
                queued += 1
            else:
                running += 1
        return Response(
            {"requested": len(picked), "queued": queued, "already_running": running},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        tags=TAGS,
        operation_id="campaign_accounts_decide",
        summary="Decide on many accounts",
        description=f"Records the same human decision (add, hold or skip) with an optional note "
        f"for up to {MAX_BULK_DECISIONS} accounts of this campaign. Each account is handled on its "
        "own: the answer lists `saved`, `unchanged` (it already had this decision and no new "
        "note) or `error` per account, so one problem never blocks the rest. Repeating a request "
        "changes nothing. Deciding never starts outreach, enrichment or an export. Needs the "
        "DECIDE level.",
        request=BulkDecisionRequestSerializer,
        responses={
            200: BulkDecisionResultSerializer,
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    )
    @action(detail=False, methods=["post"])
    def decide(self, request: Request, campaign_pk: str | None = None) -> Response:
        body = BulkDecisionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        decision = body.validated_data["decision"]
        note = body.validated_data["note"]
        ids = list(dict.fromkeys(body.validated_data["company_ids"]))
        user = request.user
        if not isinstance(user, User):
            raise PermissionDenied()
        companies = {
            c.pk: c for c in self._campaign_companies(Level.DECIDE, "all").filter(pk__in=ids)
        }
        results: list[dict[str, Any]] = []
        for company_id in ids:
            company = companies.get(company_id)
            if company is None:
                results.append({"company_id": company_id, "result": "error", "error": "Not found."})
                continue
            if company.is_archived:
                results.append({"company_id": company_id, "result": "error", "error": "Archived."})
                continue
            current = HumanDecision.objects.latest_for(company)
            if current is not None and current.decision == decision and not note:
                results.append({"company_id": company_id, "result": "unchanged", "error": ""})
                continue
            recommendation = AIRecommendation.objects.latest_for(company)
            try:
                with transaction.atomic():
                    record = record_human_decision(
                        company, decision, user, ai_recommendation=recommendation, note=note
                    )
                    record_activity(
                        company,
                        ActivityType.DECIDED,
                        user,
                        decision=record.decision,
                        note=record.note,
                        agrees_with_ai=(
                            None if recommendation is None else recommendation.status == decision
                        ),
                    )
            except DjangoValidationError as exc:
                results.append(
                    {"company_id": company_id, "result": "error", "error": " ".join(exc.messages)}
                )
                continue
            results.append({"company_id": company_id, "result": "saved", "error": ""})
        counts = {"saved": 0, "unchanged": 0, "error": 0}
        for row in results:
            counts[row["result"]] += 1
        return Response(
            {
                "saved": counts["saved"],
                "unchanged": counts["unchanged"],
                "failed": counts["error"],
                "results": results,
            }
        )

    @extend_schema(
        tags=TAGS,
        operation_id="campaign_accounts_export",
        summary="Export approved accounts (CSV)",
        description="One row per account you decided ADD: research, primary person, outreach "
        "angle and approved messages, for the tool that sends them (Brief section 22). Cells "
        "are protected against spreadsheet formulas. Contains contact details, so it needs "
        "the EDIT level.",
        responses={
            (200, "text/csv"): {"type": "string", "format": "binary"},
            **error_responses(401, 403, 404, 429, 500),
        },
    )
    @action(detail=False, methods=["get"])
    def export(self, request: Request, campaign_pk: str | None = None) -> HttpResponse:
        companies = self._campaign_companies(Level.EDIT).filter(
            _latest(HumanDecision, decision="add")
        )
        body = approved_accounts_csv(companies.order_by("name", "id"))
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="approved-accounts.csv"'
        return response

    @extend_schema(
        tags=TAGS,
        operation_id="campaign_accounts_push_to_crm",
        summary="Push approved accounts to the CRM",
        description="Pushes up to 100 accounts you decided ADD (the primary person, a tag and a "
        "note with the approved messages) to the client's CRM. Accounts that are not ready (no "
        "primary person, no email, no approved message) are skipped and listed with the reason. "
        "Needs the EDIT level.",
        request=None,
        responses={200: BulkCrmResultSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @action(detail=False, methods=["post"], url_path="push-to-crm")
    def push_to_crm(self, request: Request, campaign_pk: str | None = None) -> Response:
        campaign = self._campaign(Level.EDIT)
        companies = list(
            self._campaign_companies(Level.EDIT)
            .filter(_latest(HumanDecision, decision="add"))
            .select_related("campaign")
            .order_by("name", "id")[:100]
        )
        user = request.user if isinstance(request.user, User) else None
        return Response(BulkCrmResultSerializer(bulk_push(companies, campaign, user)).data)
