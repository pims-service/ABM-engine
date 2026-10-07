"""``/api/v1/campaigns/``: campaigns, their versioned profiles and the rules summary (issue #48).

Built on ``ClientScopedViewSet`` (docs/permissions.md). Every write goes through
``apps/campaigns/services.py`` (audited): ``create_campaign``, ``update_campaign`` (name),
``create_profile_version`` (the only way rules change), ``clone_campaign``, ``activate_campaign``,
``archive_campaign`` and ``restore_campaign``. There is no DELETE: campaigns are archived.

The same viewset also serves the nested route ``/api/v1/clients/{client_pk}/campaigns/``
(list and create only), see ``ClientCampaignViewSet``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, ClassVar, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.campaigns import services
from apps.campaigns.models import Campaign, CampaignProfile, CampaignStatus
from apps.campaigns.rules_summary import build_rules_summary
from apps.core.permissions import ClientScopedViewSet
from apps.core.roles import Level
from apps.core.schema import error_responses

from .campaign_serializers import (
    CampaignCloneSerializer,
    CampaignListQuerySerializer,
    CampaignProfileSerializer,
    CampaignSerializer,
    CampaignWriteSerializer,
    RulesSummaryQuerySerializer,
    RulesSummarySerializer,
)

TAGS = ["campaigns"]
VERSION_CREATED_HEADER = "X-Profile-Version-Created"
RULE_KEYS = frozenset(CampaignProfile.RULE_FIELDS) | {"change_note"}

_version_header = OpenApiParameter(
    VERSION_CREATED_HEADER,
    OpenApiTypes.BOOL,
    OpenApiParameter.HEADER,
    response=[200, 201],
    description="`true` when this request made a new profile version, `false` when the "
    "submitted rules equal the current version (nothing was created, status is still 200).",
)


class CampaignHasActiveJobs(APIException):
    """409: the campaign still has queued or running jobs, so it cannot be archived."""

    status_code = 409
    default_code = "campaign_has_active_jobs"
    default_detail = "Cannot archive a campaign with queued or running jobs."


@contextmanager
def api_validation() -> Iterator[None]:
    """Turn service errors into API errors: validation -> 400 with details, active jobs -> 409.

    Errors on rule fields are nested under ``profile`` to match the request body.
    """
    try:
        yield
    except services.CampaignHasActiveJobsError as exc:
        raise CampaignHasActiveJobs from exc
    except IntegrityError as exc:  # lost a race on the unique-name constraint
        if "campaigns_campaign_name_unique_active" not in str(exc):
            raise
        raise ValidationError({"name": [services.CAMPAIGN_NAME_TAKEN]}) from exc
    except DjangoValidationError as exc:
        if not hasattr(exc, "error_dict"):
            raise ValidationError({"non_field_errors": exc.messages}) from exc
        details: dict[str, Any] = {}
        rules: dict[str, Any] = {}
        for key, messages in exc.message_dict.items():
            (rules if key in RULE_KEYS else details)[key] = messages
        if rules:
            details["profile"] = rules
        raise ValidationError(details) from exc


def _levels(level: str, who: str) -> str:
    return (
        f"Needs the `{level}` level: {who}. Others get 403; campaigns of clients you are not a "
        "member of give 404."
    )


_ERRORS_WRITE = error_responses(400, 401, 403, 404, 429, 500)


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="campaigns_list",
        summary="List campaigns",
        description="Campaigns of the clients the user is a member of (all for a global "
        "admin), paginated, each with its current profile. Archived campaigns are hidden "
        "unless `archived`/`status` ask for them. Filter by `client`, or use "
        "`/clients/{client_pk}/campaigns/`. " + _levels("READ", "any member"),
        parameters=[CampaignListQuerySerializer],
        responses={200: CampaignSerializer(many=True), **error_responses(400, 401, 403, 429, 500)},
    ),
    create=extend_schema(
        tags=TAGS,
        operation_id="campaigns_create",
        summary="Create a campaign",
        description="Creates a draft campaign and its profile version 1 in one transaction. "
        "`client` is required. The name must be unique, ignoring case, among the client's "
        "non-archived campaigns. Field errors on rules are nested under `profile`. "
        "Unsupported fields (e.g. `structured_rules`) give 400. "
        + _levels("EDIT", "manager and admin, in the target client"),
        request=CampaignWriteSerializer,
        responses={201: CampaignSerializer, **_ERRORS_WRITE},
    ),
    retrieve=extend_schema(
        tags=TAGS,
        operation_id="campaigns_retrieve",
        summary="Get a campaign",
        description="The campaign with its current profile. Archived campaigns can be "
        "retrieved by id. " + _levels("READ", "any member"),
        responses={200: CampaignSerializer, **error_responses(401, 403, 404, 429, 500)},
    ),
)
class CampaignViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    ClientScopedViewSet,
):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name",)
    ordering_fields = ("name", "status", "created_at", "updated_at")
    ordering = ("name", "id")

    action_levels: ClassVar[dict[str, Level]] = {
        "list": Level.READ,
        "retrieve": Level.READ,
        "create": Level.EDIT,
        "update": Level.EDIT,
        "partial_update": Level.EDIT,
        "activate": Level.EDIT,
        "clone": Level.EDIT,
        "archive": Level.MANAGE,
        "restore": Level.MANAGE,
        "profile_versions": Level.READ,
        "profile_version": Level.READ,
        "rules_summary": Level.READ,
    }

    # ---------------------------------------------------------------- queryset

    def _user(self) -> User:
        return cast(User, self.request.user)

    def get_queryset(self) -> Any:
        queryset = super().get_queryset().select_related("current_profile")
        if getattr(self, "swagger_fake_view", False) or self.action != "list":
            return queryset  # detail routes find archived campaigns too
        query = CampaignListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        client_pk = self.kwargs.get("client_pk")
        if client_pk is not None:
            # 404 unless the client is visible; then only its campaigns.
            self.resolve_client(client_pk, Level.READ)
            queryset = queryset.filter(client_id=client_pk)
        elif "client" in params:
            queryset = queryset.filter(client_id=params["client"])
        status = params.get("status")
        archived = params.get("archived") or (
            "true" if status == CampaignStatus.ARCHIVED else "false"
        )
        if archived == "false":
            queryset = queryset.active()
        elif archived == "true":
            queryset = queryset.archived()
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def _respond(
        self, campaign: Campaign, status: int = 200, created: bool | None = None
    ) -> Response:
        response = Response(CampaignSerializer(campaign).data, status=status)
        if created is not None:
            response[VERSION_CREATED_HEADER] = "true" if created else "false"
        return response

    # ---------------------------------------------------------------- create / update

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data: Any = request.data
        client_pk = self.kwargs.get("client_pk")
        if client_pk is not None and isinstance(data, Mapping):
            client = self.resolve_client(client_pk, Level.EDIT)  # 404/403 before anything else
            if "client" in data and str(data["client"]) != str(client.pk):
                raise ValidationError({"client": ["Does not match the client in the URL."]})
            data = {**data, "client": str(client.pk)}
        serializer = CampaignWriteSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        client = self.resolve_client(values["client"], Level.EDIT)
        with api_validation():
            campaign = services.create_campaign(
                client, values["name"], values["profile"], self._user()
            )
        return self._respond(campaign, status=201, created=True)

    def _edit(self, request: Request, partial: bool) -> Response:
        campaign = self.get_object()
        serializer = CampaignWriteSerializer(
            campaign,
            data=request.data,
            partial=partial,
            context={"current_profile": campaign.current_profile},
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        created = False
        with api_validation(), transaction.atomic():
            if campaign.is_archived:
                raise DjangoValidationError("Archived campaigns are read-only.")
            if "name" in values:
                services.update_campaign(campaign, self._user(), name=values["name"])
            if values.get("profile"):
                try:
                    services.create_profile_version(campaign, values["profile"], self._user())
                    created = True
                except services.ProfileUnchangedError:
                    pass  # nothing to do: the current version already holds these rules
        campaign = self.get_queryset().get(pk=campaign.pk)
        return self._respond(campaign, created=created)

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_update",
        summary="Replace a campaign's name and rules",
        description="`name` and `profile.offer` are required; other rule fields left out keep "
        'their current value (send `[]`, `null` or `""` to clear). Changing any rule creates '
        "a NEW immutable profile version (the response shows it in `profile_version`); if the "
        "rules equal the current version nothing is created and the response is still 200 "
        f"(see the `{VERSION_CREATED_HEADER}` header). `name` changes are audited separately. "
        "An archived campaign is read-only (400). Rule field errors are nested under "
        "`profile`; unsupported fields (e.g. `structured_rules`) give 400. "
        + _levels("EDIT", "manager and admin, so reviewers cannot edit rules"),
        request=CampaignWriteSerializer,
        parameters=[_version_header],
        responses={200: CampaignSerializer, **_ERRORS_WRITE},
    )
    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._edit(request, partial=False)

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_partial_update",
        summary="Edit a campaign's name and/or rules",
        description="Same as PUT, but every field is optional. Only the rule fields sent are "
        "changed; a new profile version is made only if the result differs from the current "
        "one. " + _levels("EDIT", "manager and admin, so reviewers cannot edit rules"),
        request=CampaignWriteSerializer,
        parameters=[_version_header],
        responses={200: CampaignSerializer, **_ERRORS_WRITE},
    )
    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._edit(request, partial=True)

    # ---------------------------------------------------------------- state changes

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_activate",
        summary="Activate a draft campaign",
        description="draft -> active. 400 for an archived campaign or a campaign under an "
        "archived client. " + _levels("EDIT", "manager and admin"),
        request=None,
        responses={200: CampaignSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def activate(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        with api_validation():
            services.activate_campaign(campaign, self._user())
        return self._respond(self.get_queryset().get(pk=campaign.pk))

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_archive",
        summary="Archive a campaign",
        description="Soft delete: the campaign leaves default lists, all its data and profile "
        "versions are kept. Idempotent. 409 `campaign_has_active_jobs` while it has queued or "
        "running jobs. " + _levels("MANAGE", "admin"),
        request=None,
        responses={200: CampaignSerializer, **error_responses(401, 403, 404, 409, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        with api_validation():
            services.archive_campaign(campaign, self._user())
        return self._respond(self.get_queryset().get(pk=campaign.pk))

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_restore",
        summary="Restore an archived campaign",
        description="Back to `draft`. 400 on `name` if another active campaign of the client "
        "now uses the name, and 400 while the client is archived. " + _levels("MANAGE", "admin"),
        request=None,
        responses={200: CampaignSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def restore(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        with api_validation():
            services.restore_campaign(campaign, self._user())
        return self._respond(self.get_queryset().get(pk=campaign.pk))

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_clone",
        summary="Clone a campaign",
        description="Creates an independent draft campaign in the same client whose version 1 "
        "profile is a copy of the source's CURRENT rules. Nothing else is copied (no history, "
        "companies or jobs) and the two campaigns never share rows. The source may be "
        "archived. Audited with the source id. The name defaults to `<name> (copy)`; "
        "400 if a given name is taken or the client is archived. "
        + _levels("EDIT", "manager and admin"),
        request=CampaignCloneSerializer,
        responses={201: CampaignSerializer, **_ERRORS_WRITE},
    )
    @action(detail=True, methods=["post"])
    def clone(self, request: Request, pk: str | None = None) -> Response:
        source = self.get_object()
        serializer = CampaignCloneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with api_validation():
            copy = services.clone_campaign(
                source, self._user(), serializer.validated_data.get("name")
            )
        return self._respond(self.get_queryset().get(pk=copy.pk), status=201)

    # ---------------------------------------------------------------- profile history

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_profile_versions_list",
        summary="Profile version history",
        description="Every version of the campaign's rules, newest first, paginated. Old "
        "versions are immutable and stay retrievable. " + _levels("READ", "any member"),
        responses={
            200: CampaignProfileSerializer(many=True),
            **error_responses(401, 403, 404, 429, 500),
        },
    )
    @action(detail=True, methods=["get"], url_path="profile-versions")
    def profile_versions(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        versions = CampaignProfile.objects.filter(campaign=campaign).order_by("-version")
        page = self.paginate_queryset(versions)
        return self.get_paginated_response(CampaignProfileSerializer(page, many=True).data)

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_profile_versions_retrieve",
        summary="One profile version",
        description="The rules exactly as they were in version `version`. 404 if the campaign "
        "has no such version. " + _levels("READ", "any member"),
        parameters=[OpenApiParameter("version", OpenApiTypes.INT, OpenApiParameter.PATH)],
        responses={200: CampaignProfileSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"profile-versions/(?P<version>[0-9]+)",
        url_name="profile-version",
    )
    def profile_version(
        self, request: Request, pk: str | None = None, version: str = ""
    ) -> Response:
        campaign = self.get_object()
        profile = self._profile(campaign, int(version))
        return Response(CampaignProfileSerializer(profile).data)

    @staticmethod
    def _profile(campaign: Campaign, version: int) -> CampaignProfile:
        if version == campaign.current_profile.version:
            return campaign.current_profile
        try:
            return CampaignProfile.objects.get(campaign=campaign, version=version)
        except (CampaignProfile.DoesNotExist, OverflowError) as exc:
            raise Http404 from exc

    @extend_schema(
        tags=TAGS,
        operation_id="campaigns_rules_summary",
        summary="Rules summary (the shape AI prompts consume)",
        description="Read-only. The campaign's profile in the versioned, structured shape the "
        "qualification and message engines read: see the `RulesSummary` schema. Includes "
        "`profile_version`. `?version=N` summarises an older version; 404 if it does not "
        "exist. " + _levels("READ", "any member"),
        parameters=[RulesSummaryQuerySerializer],
        responses={200: RulesSummarySerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["get"], url_path="rules-summary")
    def rules_summary(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        query = RulesSummaryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        version = query.validated_data.get("version")
        profile = None if version is None else self._profile(campaign, version)
        return Response(RulesSummarySerializer(build_rules_summary(campaign, profile)).data)


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="client_campaigns_list",
        summary="List a client's campaigns",
        description="Same as `GET /campaigns/?client={client_pk}`, but 404 if the client is not "
        "visible to the user. " + _levels("READ", "any member"),
        parameters=[CampaignListQuerySerializer],
        responses={
            200: CampaignSerializer(many=True),
            **error_responses(400, 401, 403, 404, 429, 500),
        },
    ),
    create=extend_schema(
        tags=TAGS,
        operation_id="client_campaigns_create",
        summary="Create a campaign under a client",
        description="Same as `POST /campaigns/` with the client taken from the URL (`client` in "
        "the body is optional and must match). " + _levels("EDIT", "manager and admin"),
        request=CampaignWriteSerializer,
        responses={201: CampaignSerializer, **_ERRORS_WRITE},
    ),
)
class ClientCampaignViewSet(CampaignViewSet):
    """The nested route ``/clients/{client_pk}/campaigns/`` (only list and create are routed)."""
