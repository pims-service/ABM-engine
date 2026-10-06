"""``/api/v1/clients/``: list, create, retrieve, update and archive/restore clients (issue #47).

Built on ``ClientScopedViewSet`` (docs/permissions.md): clients the user is not a member of do
not exist for them (404), the role decides the rest. Every write goes through the service
layer in ``apps/campaigns/services.py``; there is no DELETE, a client is archived instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, mixins, serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.campaigns import services
from apps.campaigns.models import Client, ClientStatus
from apps.core.permissions import ClientScopedViewSet
from apps.core.roles import Level
from apps.core.schema import error_responses

from .client_serializers import ClientListQuerySerializer, ClientSerializer

TAGS = ["clients"]


@contextmanager
def api_validation() -> Iterator[None]:
    """Turn a service ``ValidationError`` into a DRF one (400 with field details)."""
    try:
        yield
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise ValidationError(exc.message_dict) from exc
        raise ValidationError({"non_field_errors": exc.messages}) from exc


def _levels(level: str, who: str) -> str:
    return (
        f"Needs the `{level}` level: {who}. Others get 403; clients you are not a member of "
        "give 404."
    )


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="clients_list",
        summary="List clients",
        description="Clients the user is a member of (all clients for a global admin), "
        "paginated. Archived clients are hidden unless `archived`/`status` ask for them. "
        + _levels("READ", "any member"),
        parameters=[ClientListQuerySerializer],
        responses={200: ClientSerializer(many=True), **error_responses(400, 401, 403, 429, 500)},
    ),
    create=extend_schema(
        tags=TAGS,
        operation_id="clients_create",
        summary="Create a client",
        description="Creates an active client; the creator becomes its admin member. Needs "
        "the `MANAGE` level in at least one client (or global admin): there is no client to "
        "check yet, so this is the platform-level rule. Name must be unique, ignoring case, "
        "among non-archived clients.",
        responses={201: ClientSerializer, **error_responses(400, 401, 403, 429, 500)},
    ),
    retrieve=extend_schema(
        tags=TAGS,
        operation_id="clients_retrieve",
        summary="Get a client",
        description="Archived clients can be retrieved by id. " + _levels("READ", "any member"),
        responses={200: ClientSerializer, **error_responses(401, 403, 404, 429, 500)},
    ),
    update=extend_schema(
        tags=TAGS,
        operation_id="clients_update",
        summary="Replace a client's name and notes",
        description="An archived client is read-only (400). "
        + _levels("EDIT", "manager and admin"),
        responses={200: ClientSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    ),
    partial_update=extend_schema(
        tags=TAGS,
        operation_id="clients_partial_update",
        summary="Edit a client's name and/or notes",
        description="An archived client is read-only (400). "
        + _levels("EDIT", "manager and admin"),
        responses={200: ClientSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    ),
)
class ClientViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    ClientScopedViewSet,
):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name",)
    ordering_fields = ("name", "status", "created_at", "updated_at")
    ordering = ("name", "id")

    # Creating needs MANAGE in some client (global admins always pass); the creator becomes admin
    # of the new client. Editing name/notes is EDIT; archiving and restoring is MANAGE.
    action_levels: ClassVar[dict[str, Level]] = {
        "list": Level.READ,
        "retrieve": Level.READ,
        "create": Level.MANAGE,
        "update": Level.EDIT,
        "partial_update": Level.EDIT,
        "archive": Level.MANAGE,
        "restore": Level.MANAGE,
    }

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False) or self.action != "list":
            return queryset  # detail routes find archived clients too
        query = ClientListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        status = params.get("status")
        archived = params.get("archived") or (
            "true" if status == ClientStatus.ARCHIVED else "false"
        )
        if archived == "false":
            queryset = queryset.active()
        elif archived == "true":
            queryset = queryset.archived()
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def perform_create(self, serializer: serializers.BaseSerializer[Client]) -> None:
        data = serializer.validated_data
        with api_validation():
            serializer.instance = services.create_client(
                data["name"], data.get("notes", ""), cast(User, self.request.user)
            )

    def perform_update(self, serializer: serializers.BaseSerializer[Client]) -> None:
        with api_validation():
            services.update_client(cast(Client, serializer.instance), serializer.validated_data)

    @extend_schema(
        tags=TAGS,
        operation_id="clients_archive",
        summary="Archive a client",
        description="Soft delete: the client leaves default lists, all its data is kept. "
        "Idempotent. Campaigns are not changed. " + _levels("MANAGE", "admin"),
        request=None,
        responses={200: ClientSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request: Request, pk: str | None = None) -> Response:
        client = self.get_object()
        with api_validation():
            services.archive_client(client)
        return Response(ClientSerializer(client).data)

    @extend_schema(
        tags=TAGS,
        operation_id="clients_restore",
        summary="Restore an archived client",
        description="400 on `name` if another active client now uses the name. "
        + _levels("MANAGE", "admin"),
        request=None,
        responses={200: ClientSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def restore(self, request: Request, pk: str | None = None) -> Response:
        client = self.get_object()
        with api_validation():
            services.restore_client(client)
        return Response(ClientSerializer(client).data)
