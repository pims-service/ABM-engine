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
from rest_framework import filters, mixins, status
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

from .serializers import (
    AnalysisRequestSerializer,
    CompanyDetailSerializer,
    CompanyListQuerySerializer,
    CompanySerializer,
    ManualCompanySerializer,
    ManualEntryResultSerializer,
)

TAGS = ["companies"]


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
