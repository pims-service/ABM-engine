"""``/api/v1/campaigns/{id}/companies/`` and ``/api/v1/companies/``: manual company entry, the
company list and detail, archive/restore and "Analyze Company" (issue #59).

Built on ``ClientScopedViewSet`` (docs/permissions.md). Writes go through the services:
``apps.imports.manual.add_company_manually`` (a one-row manual ImportBatch, dedupe, audit),
``apps.companies.services.archive_company`` / ``restore_company`` and
``apps.companies.analysis.request_analysis``. Companies are never edited and never deleted.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies import services
from apps.companies.analysis import latest_analysis_jobs, request_analysis
from apps.companies.models import Company
from apps.core.models import Job
from apps.core.permissions import ClientScopedViewSet, request_roles
from apps.core.roles import Level, role_allows
from apps.core.schema import error_responses
from apps.core.tenancy import is_global_admin
from apps.imports.manual import (
    ManualEntry,
    add_company_manually,
    field_errors,
    validate_manual_entry,
)
from apps.imports.models import ImportRowOutcome
from apps.imports.services import COMPANY_REJECTED
from apps.outreach.messages_api import (
    CrmPushRequestSerializer,
    CrmPushResultSerializer,
    GenerateRequestSerializer,
    GenerateResultSerializer,
    MessageEditSerializer,
    MessageRefSerializer,
    MessageSerializer,
    approve_for,
    company_messages,
    edit_for,
    export_for,
    generate_for,
    message_body,
    push_for,
)
from apps.outreach.models import ActivityType
from apps.outreach.services import record_activity, record_feedback
from apps.research.card import (
    ActivityItemSerializer,
    DecisionRequestSerializer,
    FeedbackRequestSerializer,
    ResearchCardSerializer,
    build_activity,
    build_card,
)
from apps.research.commercial import CommercialTeamRequestSerializer, record_commercial_team
from apps.research.models import AIRecommendation
from apps.research.services import record_human_decision

from .contacts import (
    ContactCreateSerializer,
    ContactPatchSerializer,
    ContactRefSerializer,
    ContactSerializer,
    add_contact_to,
    company_contacts,
    contact_body,
    find_contact,
    update_contact_of,
)
from .pipeline import STAGE_ACTIVITY, record_outreach
from .serializers import (
    AnalysisRequestSerializer,
    CompanyDetailSerializer,
    CompanyListQuerySerializer,
    CompanySerializer,
    ManualCompanySerializer,
    ManualEntryResultSerializer,
)
from .website_research import WebsiteResearchSerializer, research_website

TAGS = ["companies"]


class OutreachStatusRequestSerializer(serializers.Serializer[Any]):
    stage = serializers.ChoiceField(choices=list(STAGE_ACTIVITY))
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


class OutreachStatusSerializer(serializers.Serializer[Any]):
    outreach_status = serializers.CharField()


@contextmanager
def api_validation() -> Iterator[None]:
    """Turn service ``ValidationError`` into a 400 in the standard envelope."""
    try:
        yield
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise ValidationError(exc.message_dict) from exc
        raise ValidationError({"non_field_errors": exc.messages}) from exc


def _levels(level: str, who: str) -> str:
    return (
        f"Needs the `{level}` level: {who}. Others get 403; companies of clients you are not a "
        "member of give 404."
    )


def attach_analysis(companies: list[Company]) -> None:
    """Set ``analysis_job`` (newest analysis job or None) on each company: one query."""
    jobs = latest_analysis_jobs(c.pk for c in companies)
    for company in companies:
        company.analysis_job = jobs.get(str(company.pk))  # type: ignore[attr-defined]


def _one(company: Company, job: Job | None = None) -> Company:
    company.analysis_job = job  # type: ignore[attr-defined]
    return company


_ERRORS_WRITE = error_responses(400, 401, 403, 404, 429, 500)


class CompanyBaseViewSet(ClientScopedViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

    def _user(self) -> User:
        return cast(User, self.request.user)

    def get_queryset(self) -> Any:
        return super().get_queryset().select_related("possible_duplicate_of")

    def _company_response(self, company: Company, status_code: int = 200) -> Response:
        company = self.get_queryset().get(pk=company.pk)
        attach_analysis([company])
        return Response(CompanyDetailSerializer(company).data, status=status_code)


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="campaign_companies_list",
        summary="List a campaign's companies",
        description="Companies of one campaign, paginated, each with its newest analysis job "
        "(`analysis`) and the possible-duplicate pointer. Archived companies are hidden unless "
        "`archived` asks for them. `search` matches name, domain and website. "
        + _levels("READ", "any member of the campaign's client"),
        parameters=[CompanyListQuerySerializer],
        responses={
            200: CompanySerializer(many=True),
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    ),
    create=extend_schema(
        tags=TAGS,
        operation_id="campaign_companies_create",
        summary="Add one company by hand",
        description="Manual entry (Brief section 4A). Send `name` and a `website` and/or "
        "`profile_url` (`country` optional), or `input` for one free-text box; `analyze: true` "
        "also queues an analysis. The entry runs through a one-row manual import batch: "
        "**201** when a company was created (a weak name-and-country match still creates it, "
        "flagged `possible_duplicate`); **200** when a strong match (same domain or profile) "
        "already exists in the campaign: that company is returned **unchanged** with "
        "`duplicate: true` (an archived one is restored: `restored: true`). Field errors use the "
        "stable codes of the import schema: `details.<field>[].code`. Nothing is created for "
        "invalid input. Archived campaigns and clients take no companies (400). "
        + _levels("EDIT", "manager and admin, in the campaign's client"),
        request=ManualCompanySerializer,
        responses={
            201: ManualEntryResultSerializer,
            200: ManualEntryResultSerializer,
            **_ERRORS_WRITE,
        },
    ),
)
class CampaignCompanyViewSet(mixins.ListModelMixin, CompanyBaseViewSet):
    """The nested route ``/campaigns/{campaign_pk}/companies/`` (list and create)."""

    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name", "domain", "website")
    ordering_fields = ("name", "status", "created_at", "updated_at")
    ordering = ("name", "id")

    action_levels: ClassVar[dict[str, Level]] = {
        "list": Level.READ,
        "create": Level.EDIT,
    }

    def _campaign(self, level: Level) -> Campaign:
        """The campaign of the URL: 404 unless visible, 403 when the role is below ``level``."""
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

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False) or self.action != "list":
            return queryset
        campaign = self._campaign(Level.READ)
        query = CompanyListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        queryset = queryset.filter(campaign_id=campaign.pk)
        archived = params.get("archived", "false")
        if archived == "false":
            queryset = queryset.active()
        elif archived == "true":
            queryset = queryset.archived()
        if "status" in params:
            queryset = queryset.filter(status=params["status"])
        if "input_source" in params:
            queryset = queryset.filter(input_source=params["input_source"])
        if "possible_duplicate" in params:
            flagged = params["possible_duplicate"] == "true"
            queryset = queryset.filter(possible_duplicate_of__isnull=not flagged)
        return queryset

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        rows = list(page) if page is not None else []
        attach_analysis(rows)
        return self.get_paginated_response(CompanySerializer(rows, many=True).data)

    # ---------------------------------------------------------------- create

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        campaign = self._campaign(Level.EDIT)  # 404/403 before anything else
        serializer = ManualCompanySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        analyze = bool(values.pop("analyze", False))
        raw, errors = validate_manual_entry(values)
        if errors is not None:
            raise ValidationError(field_errors(errors))
        with api_validation():
            entry = add_company_manually(campaign, raw, user=self._user())
            if entry.failed:
                if entry.row.error_code != COMPANY_REJECTED:
                    raise RuntimeError(f"Manual entry failed: {entry.row.error_code}")
                raise DjangoValidationError(entry.row.error_message)
            company = cast(Company, entry.company)
            job = request_analysis(company, self._user()).job if analyze else None
        return Response(
            self._entry_body(entry, company, job),
            status=status.HTTP_201_CREATED
            if entry.row.outcome == ImportRowOutcome.CREATED
            else status.HTTP_200_OK,
        )

    def _entry_body(self, entry: ManualEntry, company: Company, job: Job | None) -> dict[str, Any]:
        company = self.get_queryset().get(pk=company.pk)
        if job is None:
            attach_analysis([company])
        else:
            _one(company, job)
        row = entry.row
        outcome = row.outcome
        return dict(
            ManualEntryResultSerializer(
                {
                    "outcome": outcome,
                    "created": outcome == ImportRowOutcome.CREATED,
                    "duplicate": outcome in (ImportRowOutcome.DUPLICATE, ImportRowOutcome.RESTORED),
                    "restored": outcome == ImportRowOutcome.RESTORED,
                    "possible_duplicate": entry.possible_duplicate,
                    "possible_duplicate_of": company.possible_duplicate_of
                    if entry.possible_duplicate
                    else None,
                    "company": company,
                    "batch_id": entry.batch.pk,
                    "import_row": row,
                    "analysis": company.analysis_job,  # type: ignore[attr-defined]
                }
            ).data
        )


@extend_schema_view(
    retrieve=extend_schema(
        tags=TAGS,
        operation_id="companies_retrieve",
        summary="Get a company",
        description="The company with its newest analysis job, its latest research snapshot "
        "(`latest_research`, null until research exists) and the possible-duplicate pointer. "
        "Archived companies can be retrieved by id. " + _levels("READ", "any member"),
        responses={200: CompanyDetailSerializer, **error_responses(401, 403, 404, 429, 500)},
    ),
)
class CompanyViewSet(mixins.RetrieveModelMixin, CompanyBaseViewSet):
    """``/companies/{id}/``: detail, archive, restore and analyze (no list, update or delete)."""

    serializer_class = CompanyDetailSerializer

    action_levels: ClassVar[dict[str, Level]] = {
        "retrieve": Level.READ,
        "analyze": Level.EDIT,
        "website_research": Level.EDIT,
        "card": Level.READ,
        "decision": Level.DECIDE,
        "activity": Level.READ,
        "contacts": Level.READ,
        "add_contact": Level.EDIT,
        "update_contact": Level.EDIT,
        "archive_contact": Level.EDIT,
        "messages": Level.READ,
        "generate_messages": Level.EDIT,
        "approve_message": Level.DECIDE,
        "edit_message": Level.DECIDE,
        "export_message": Level.DECIDE,
        "feedback": Level.DECIDE,
        "outreach_status": Level.EDIT,
        "commercial_team": Level.EDIT,
        "push_to_crm": Level.EDIT,
        "archive": Level.MANAGE,
        "restore": Level.MANAGE,
    }

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._company_response(self.get_object())

    @extend_schema(
        tags=TAGS,
        operation_id="companies_analyze",
        summary="Analyze Company",
        description="Queues an analysis of the company as a background job (`company_analysis`, "
        "scoped to the company's client and campaign) and answers **202** with the job. "
        "Idempotent: while the company has a queued or running analysis job that job is returned "
        "(`already_queued: true`) and nothing is queued again. 400 for an archived company, "
        "campaign or client. **The research engine is not built yet (milestone M4):** the "
        'worker task is a placeholder that ends the job `failed` with a clear "not implemented '
        'yet" message in `error_summary`. It researches nothing and invents no data. '
        + _levels("EDIT", "manager and admin"),
        request=None,
        responses={202: AnalysisRequestSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def analyze(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        with api_validation():
            result = request_analysis(company, self._user())
        body = AnalysisRequestSerializer(
            {
                "company_id": company.pk,
                "job": result.job,
                "already_queued": not result.created,
            }
        ).data
        return Response(body, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_website_research",
        summary="Research the company website",
        description="Reads the company's public website now (homepage plus up to three of: about, "
        "products, careers, news, contact) with the safe fetcher: robots.txt is honoured, social "
        "and professional networks are never fetched, private addresses are refused. Returns the "
        "pages with title, description, a text excerpt and their provenance (URL, time, content "
        "hash). Nothing is stored or inferred. A site that cannot be read is reported in `error` "
        "(still **200**). Can take up to about a minute. " + _levels("EDIT", "manager and admin"),
        request=None,
        responses={200: WebsiteResearchSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="website-research")
    def website_research(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        return Response(WebsiteResearchSerializer(research_website(company)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_card",
        summary="Account research card",
        description="Everything a reviewer needs on one page (Brief section 10): the latest "
        "research facts with their source, the fresh signals (Trigger yes or no is computed from "
        "them), the ICP assessment, the AI recommendation, the latest human decision and the "
        "latest analysis job. Facts that were not found are null. `gaps` lists what needs a data "
        "provider that is not connected yet. " + _levels("READ", "any member"),
        responses={200: ResearchCardSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["get"])
    def card(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        return Response(ResearchCardSerializer(build_card(company)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_decision",
        summary="Record the human decision",
        description="Add, hold or skip (Brief section 11). Stored separately from the AI "
        "recommendation, which is never changed; the decision points at the recommendation it "
        "answered (if any) so agreement can be measured. Changing your mind adds a new decision. "
        "Returns the updated card. 400 for an archived company. "
        + _levels("DECIDE", "reviewer, manager and admin"),
        request=DecisionRequestSerializer,
        responses={200: ResearchCardSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def decision(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = DecisionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if company.is_archived:
            raise ValidationError({"non_field_errors": ["Archived companies are read-only."]})
        with api_validation():
            recommendation = AIRecommendation.objects.latest_for(company)
            record = record_human_decision(
                company,
                body.validated_data["decision"],
                self._user(),
                ai_recommendation=recommendation,
                note=body.validated_data["note"],
            )
            record_activity(
                company,
                ActivityType.DECIDED,
                self._user(),
                decision=record.decision,
                note=record.note,
                agrees_with_ai=(
                    None if recommendation is None else recommendation.status == record.decision
                ),
            )
        return Response(ResearchCardSerializer(build_card(company)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_activity",
        summary="Activity timeline",
        description="What happened to this company, newest first (Brief section 14): research "
        "runs, AI recommendations, human decisions and feedback. The latest 50. "
        + _levels("READ", "any member"),
        responses={
            200: ActivityItemSerializer(many=True),
            **error_responses(401, 403, 404, 429, 500),
        },
    )
    @action(detail=True, methods=["get"], pagination_class=None)
    def activity(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        return Response(ActivityItemSerializer(build_activity(company), many=True).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_feedback",
        summary="Flag something the system got wrong",
        description="Brief section 16: wrong buyer, not B2B, unsuitable industry, a signal that "
        "should not count, CEO as primary or not, government company, incorrect data, other "
        "(needs a note). Optionally point at one signal, contact, angle or message of this "
        "company. Stored on the timeline for later rule and prompt improvements; it changes "
        "nothing by itself. Returns the new timeline entry. "
        + _levels("DECIDE", "reviewer, manager and admin"),
        request=FeedbackRequestSerializer,
        responses={201: ActivityItemSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def feedback(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = FeedbackRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        target = None
        if data.get("target_type") and data.get("target_id"):
            target = {"type": data["target_type"], "id": str(data["target_id"])}
        if company.is_archived:
            raise ValidationError({"non_field_errors": ["Archived companies are read-only."]})
        with api_validation():
            record_feedback(
                company, data["kind"], target=target, note=data["note"], actor=self._user()
            )
        latest = build_activity(company, limit=1)[0]
        return Response(ActivityItemSerializer(latest).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_contacts",
        summary="People at the company",
        description="Active contacts, best first: primary, secondary, then by rank (Brief "
        "section 8). " + _levels("READ", "any member"),
        responses={200: ContactSerializer(many=True), **error_responses(401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["get"], pagination_class=None)
    def contacts(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        rows = [contact_body(c) for c in company_contacts(company)]
        return Response(ContactSerializer(rows, many=True).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_add_contact",
        summary="Add a person by hand",
        description="Adds a contact with a manual data source that records who typed it. "
        "Taking the primary or secondary slot demotes the previous holder to none. "
        + _levels("EDIT", "manager and admin"),
        request=ContactCreateSerializer,
        responses={201: ContactSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="add-contact")
    def add_contact(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = ContactCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with api_validation():
            contact = add_contact_to(company, dict(body.validated_data), self._user())
        return Response(ContactSerializer(contact_body(contact)).data, status=201)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_update_contact",
        summary="Edit a person",
        description="Change name, title, profile URL, email, why relevant, rank or role of one "
        "contact of this company. " + _levels("EDIT", "manager and admin"),
        request=ContactPatchSerializer,
        responses={200: ContactSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="update-contact")
    def update_contact(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = ContactPatchSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        with api_validation():
            contact = update_contact_of(company, dict(body.validated_data))
        return Response(ContactSerializer(contact_body(contact)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_archive_contact",
        summary="Remove a person",
        description="Archives the contact (never deleted). " + _levels("EDIT", "manager and admin"),
        request=ContactRefSerializer,
        responses={204: None, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="archive-contact")
    def archive_contact(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = ContactRefSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        find_contact(company, body.validated_data["contact_id"]).archive()
        return Response(status=204)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_messages",
        summary="Outreach messages",
        description="The current messages of this company (Brief section 12): per person and "
        "channel, message before follow-up, with the signals each one cites. Edited messages "
        "are replaced by their newest version. " + _levels("READ", "any member"),
        responses={
            200: MessageSerializer(many=True),
            **error_responses(401, 403, 404, 429, 500),
        },
    )
    @action(detail=True, methods=["get"], pagination_class=None)
    def messages(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        rows = [message_body(m) for m in company_messages(company)]
        return Response(MessageSerializer(rows, many=True).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_generate_messages",
        summary="Write message drafts",
        description="Asks the AI for a LinkedIn message and/or a cold email (with an optional "
        "follow-up) to one person, in one of the campaign's languages. Needs a human ADD "
        "decision first (checked before the AI is called), an outreach angle (run the "
        "analysis) and the client's AI connection. The AI sees only the stored research; "
        "drafts that cite an event that was not provided, or contain numbers that are not in "
        "the research, are refused and listed in `rejected`. Drafts only: nothing is sent. "
        + _levels("EDIT", "manager and admin"),
        request=GenerateRequestSerializer,
        responses={201: GenerateResultSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="generate-messages")
    def generate_messages(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = GenerateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with api_validation():
            result = generate_for(company, dict(body.validated_data), self._user())
        return Response(GenerateResultSerializer(result).data, status=201)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_approve_message",
        summary="Approve a message",
        description="Draft to approved, by you (Brief section 11). Needs a human ADD decision. "
        + _levels("DECIDE", "reviewer, manager and admin"),
        request=MessageRefSerializer,
        responses={200: MessageSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="approve-message")
    def approve_message(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = MessageRefSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        message = approve_for(company, body.validated_data["message_id"], self._user())
        return Response(MessageSerializer(message_body(message)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_edit_message",
        summary="Edit a message",
        description="Writes your wording as a new draft that replaces the old message; the old "
        "one is kept. The new draft needs approval again. "
        + _levels("DECIDE", "reviewer, manager and admin"),
        request=MessageEditSerializer,
        responses={200: MessageSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="edit-message")
    def edit_message(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = MessageEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        message = edit_for(company, dict(body.validated_data), self._user())
        return Response(MessageSerializer(message_body(message)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_export_message",
        summary="Mark a message as exported",
        description="Approved to exported, once you have copied or sent it from your own tool. "
        "Adds an entry to the timeline. This product sends nothing itself. "
        + _levels("DECIDE", "reviewer, manager and admin"),
        request=MessageRefSerializer,
        responses={200: MessageSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="export-message")
    def export_message(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = MessageRefSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        message = export_for(company, body.validated_data["message_id"], self._user())
        return Response(MessageSerializer(message_body(message)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_outreach_status",
        summary="Record outreach progress",
        description="Log that the account was contacted, replied or booked a meeting (the "
        "furthest step shows on the dashboard). Only for an account you decided ADD for. "
        + _levels("EDIT", "manager and admin"),
        request=OutreachStatusRequestSerializer,
        responses={200: OutreachStatusSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="outreach-status")
    def outreach_status(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = OutreachStatusRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with api_validation():
            stage = record_outreach(
                company, body.validated_data["stage"], body.validated_data["note"], self._user()
            )
        return Response({"outreach_status": stage})

    @extend_schema(
        tags=TAGS,
        operation_id="companies_commercial_team",
        summary="Enter the commercial team's numbers",
        description="Type what you found: current headcounts of the company and of its sales, "
        "business development, marketing and commercial teams, and the same numbers 3, 6 and "
        "12 months ago if you know them. Needs a source note (where the numbers come from) and "
        "the date they were true. Saved as a new research snapshot (history is kept) and the "
        "3, 6 and 12 month growth is calculated from your numbers; a window without data stays "
        "empty. Returns the updated card. Until a company-data provider is connected this is "
        "how the commercial team section is filled. " + _levels("EDIT", "manager and admin"),
        request=CommercialTeamRequestSerializer,
        responses={200: ResearchCardSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="commercial-team")
    def commercial_team(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = CommercialTeamRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        record_commercial_team(company, dict(body.validated_data), self._user())
        return Response(ResearchCardSerializer(build_card(company)).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_push_to_crm",
        summary="Push the approved account to the CRM",
        description="Brief section 12: after you approve a message, push the primary person to the "
        "client's CRM (GoHighLevel). The CRM gets the person, the company name, a tag and one "
        "note with the research summary, the angle and the approved messages. Needs a human ADD "
        "decision, a primary person with an email address and at least one approved message; "
        "the approved messages are then marked exported. Pushing again updates the same CRM "
        "contact and does not add the note twice (unless `refresh_note`). "
        + _levels("EDIT", "manager and admin"),
        request=CrmPushRequestSerializer,
        responses={200: CrmPushResultSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"], url_path="push-to-crm")
    def push_to_crm(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        body = CrmPushRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        result = push_for(company, body.validated_data["refresh_note"], self._user())
        return Response(CrmPushResultSerializer(result).data)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_archive",
        summary="Archive a company",
        description="Soft delete: the company leaves default lists; its research and history are "
        "kept. Idempotent. 400 inside an archived campaign or client. "
        + _levels("MANAGE", "admin"),
        request=None,
        responses={200: CompanyDetailSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        with api_validation():
            services.archive_company(company, self._user())
        return self._company_response(company)

    @extend_schema(
        tags=TAGS,
        operation_id="companies_restore",
        summary="Restore an archived company",
        description="Idempotent. 400 inside an archived campaign or client. "
        + _levels("MANAGE", "admin"),
        request=None,
        responses={200: CompanyDetailSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def restore(self, request: Request, pk: str | None = None) -> Response:
        company = self.get_object()
        with api_validation():
            services.restore_company(company, self._user())
        return self._company_response(company)
