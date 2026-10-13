"""``/api/v1/integrations/connections/``: a client's provider connections (issue #76).

Built on ``ClientScopedViewSet`` (docs/permissions.md). Credentials are sensitive, so the
levels are stricter than the rest of the client API:

* read (list, retrieve): ``EDIT`` (manager and admin). Viewers and reviewers get 403 and a list
  only contains clients where the caller is manager or admin.
* write (create, update, enable/disable, archive/restore): ``MANAGE`` (admin).

Credentials are write-only: no response ever contains them (only ``has_credentials`` and a
masked hint). Every write goes through ``apps/integrations/services.py`` (audited, no values).
There is no DELETE: connections are archived.
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
from apps.core.permissions import ClientScopedViewSet, request_roles
from apps.core.roles import Level, role_allows
from apps.core.schema import error_responses
from apps.core.tenancy import is_global_admin
from apps.integrations import services, usage
from apps.integrations.models import IntegrationConnection

from .serializers import (
    ConnectionCreateSerializer,
    ConnectionListQuerySerializer,
    ConnectionSerializer,
    ConnectionUpdateSerializer,
)
from .usage_serializers import BudgetSerializer, BudgetWriteSerializer

TAGS = ["integrations"]


@contextmanager
def api_validation() -> Iterator[None]:
    """Turn service validation errors into 400s in the standard envelope."""
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


_READ = _levels("EDIT", "manager and admin")
_WRITE = _levels("MANAGE", "admin")


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="integration_connections_list",
        summary="List integration connections",
        description="Connections of the clients where you are manager or admin (all for a "
        "global admin), paginated. Archived ones are hidden unless `archived` asks for them. "
        "Credentials are never returned: see `has_credentials` and `credentials_hint`. " + _READ,
        parameters=[ConnectionListQuerySerializer],
        responses={
            200: ConnectionSerializer(many=True),
            **error_responses(400, 401, 403, 429, 500),
        },
    ),
    create=extend_schema(
        tags=TAGS,
        operation_id="integration_connections_create",
        summary="Create an integration connection",
        description="Optionally with its first `credentials` (write-only, encrypted at rest). "
        "The name must be unique per client, category and provider among non-archived "
        "connections (case-insensitive). 404 for a client you cannot see. " + _WRITE,
        request=ConnectionCreateSerializer,
        responses={201: ConnectionSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    ),
    retrieve=extend_schema(
        tags=TAGS,
        operation_id="integration_connections_retrieve",
        summary="Get an integration connection",
        description="Archived connections can be retrieved by id. " + _READ,
        responses={200: ConnectionSerializer, **error_responses(401, 403, 404, 429, 500)},
    ),
    update=extend_schema(
        tags=TAGS,
        operation_id="integration_connections_update",
        summary="Change a connection's name, config and/or credentials",
        description="`credentials`, when sent, replace all stored credentials; when omitted "
        "they are kept. Category and provider cannot change. An archived connection is "
        "read-only (400). " + _WRITE,
        request=ConnectionUpdateSerializer,
        responses={200: ConnectionSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    ),
    partial_update=extend_schema(
        tags=TAGS,
        operation_id="integration_connections_partial_update",
        summary="Change a connection's name, config and/or credentials",
        description="Same as the full update; every field is optional. " + _WRITE,
        request=ConnectionUpdateSerializer,
        responses={200: ConnectionSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    ),
)
class ConnectionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    ClientScopedViewSet,
):
    queryset = IntegrationConnection.objects.all()
    serializer_class = ConnectionSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name", "provider")
    ordering_fields = ("name", "category", "provider", "status", "created_at", "updated_at")
    ordering = ("category", "name", "id")

    action_levels: ClassVar[dict[str, Level]] = {
        "list": Level.EDIT,
        "retrieve": Level.EDIT,
        "create": Level.MANAGE,
        "update": Level.MANAGE,
        "partial_update": Level.MANAGE,
        "enable": Level.MANAGE,
        "disable": Level.MANAGE,
        "archive": Level.MANAGE,
        "restore": Level.MANAGE,
        "budget": Level.EDIT,
        "set_budget": Level.MANAGE,
    }

    def _user(self) -> User:
        return cast(User, self.request.user)

    def get_serializer_class(self) -> Any:
        if self.action == "create":
            return ConnectionCreateSerializer
        if self.action in ("update", "partial_update"):
            return ConnectionUpdateSerializer
        return ConnectionSerializer

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False) or self.action != "list":
            return queryset  # detail routes find archived rows; roles are checked per object
        # A list shows only clients where the caller may read connections (manager, admin), so a
        # viewer's list never contains rows they could not open.
        if not is_global_admin(self.request.user):
            allowed = [
                client_id
                for client_id, role in request_roles(self.request).items()
                if role_allows(role, Level.EDIT)
            ]
            queryset = queryset.filter(client_id__in=allowed)
        query = ConnectionListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        for name in ("client", "category", "provider", "status"):
            if name in params:
                queryset = queryset.filter(
                    **{f"{name}_id" if name == "client" else name: params[name]}
                )
        if "test_status" in params:
            queryset = queryset.filter(last_test_status=params["test_status"])
        archived = params.get("archived", "false")
        if archived == "false":
            queryset = queryset.active()
        elif archived == "true":
            queryset = queryset.archived()
        return queryset

    # ---------------------------------------------------------------- writes

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = ConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        client = self.resolve_client(data["client"], Level.MANAGE)  # 404 / 403 first
        with api_validation():
            connection = services.create_connection(
                client,
                self._user(),
                category=data["category"],
                provider=data["provider"],
                name=data["name"],
                config=data.get("config"),
                credentials=data.get("credentials"),
                status=data["status"],
            )
        return Response(ConnectionSerializer(connection).data, status=201)

    def perform_update(self, serializer: serializers.BaseSerializer[Any]) -> None:
        values = dict(serializer.validated_data)
        credentials = values.pop("credentials", None)
        with api_validation():
            services.update_connection(
                cast(IntegrationConnection, serializer.instance),
                self._user(),
                credentials=credentials,
                **values,
            )

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(ConnectionSerializer(instance).data)

    def _transition(self, change: Any) -> Response:
        connection = self.get_object()
        with api_validation():
            change(connection, self._user())
        return Response(ConnectionSerializer(connection).data)

    @extend_schema(
        tags=TAGS,
        operation_id="integration_connections_enable",
        summary="Enable a connection",
        description="Sets the status to `active` (also clears `error`). Idempotent. " + _WRITE,
        request=None,
        responses={200: ConnectionSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def enable(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(services.enable_connection)

    @extend_schema(
        tags=TAGS,
        operation_id="integration_connections_disable",
        summary="Disable a connection",
        description="Adapters stop resolving it; settings and credentials are kept. "
        "Idempotent. " + _WRITE,
        request=None,
        responses={200: ConnectionSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def disable(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(services.disable_connection)

    @extend_schema(
        tags=TAGS,
        operation_id="integration_connections_archive",
        summary="Archive a connection",
        description="Soft delete: it leaves default lists and adapters stop using it. "
        "Idempotent. " + _WRITE,
        request=None,
        responses={200: ConnectionSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(services.archive_connection)

    @extend_schema(
        tags=TAGS,
        operation_id="integration_connections_restore",
        summary="Restore an archived connection",
        description="400 on `name` if another active connection now uses the name. " + _WRITE,
        request=None,
        responses={200: ConnectionSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["post"])
    def restore(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(services.restore_connection)

    # ---------------------------------------------------------------- budget (issue #79)

    @extend_schema(
        tags=TAGS,
        operation_id="integration_connections_budget_retrieve",
        summary="Get the monthly budget of a connection",
        description="The limits (none when unset: `configured` is false) and this month's usage "
        "against them (UTC calendar month), with the `state` the next call would get. " + _READ,
        responses={200: BudgetSerializer, **error_responses(401, 403, 404, 429, 500)},
    )
    @action(detail=True, methods=["get"], url_path="budget")
    def budget(self, request: Request, pk: str | None = None) -> Response:
        return Response(BudgetSerializer(usage.budget_summary(self.get_object())).data)

    @extend_schema(
        tags=TAGS,
        operation_id="integration_connections_budget_update",
        summary="Set the monthly budget of a connection",
        description="Replaces the whole budget: omitted limits mean no cap. At `warn_at_percent` "
        "of a limit calls are logged as warnings; at the limit and with `hard_stop` they are "
        "refused (`budget_exceeded`) before any provider request. Audited. An archived "
        "connection answers 400. " + _WRITE,
        request=BudgetWriteSerializer,
        responses={200: BudgetSerializer, **error_responses(400, 401, 403, 404, 429, 500)},
    )
    @budget.mapping.put
    def set_budget(self, request: Request, pk: str | None = None) -> Response:
        connection = self.get_object()
        serializer = BudgetWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with api_validation():
            usage.set_budget(
                connection,
                self._user(),
                credit_limit=data.get("credit_limit"),
                cost_limit=data.get("cost_limit"),
                currency=data.get("currency", ""),
                warn_at_percent=data["warn_at_percent"],
                hard_stop=data["hard_stop"],
            )
        return Response(BudgetSerializer(usage.budget_summary(connection)).data)
