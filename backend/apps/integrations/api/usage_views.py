"""``/api/v1/integrations/usage/``: what provider calls cost (issue #79). Read-only.

Built on ``ClientScopedViewSet``. Usage is operational and commercial data, so reading needs the
``EDIT`` level (manager and admin), like connections; a list only contains clients where the caller
is manager or admin. Budgets are on the connection (``/connections/{id}/budget/``).

* ``GET /usage/``: the call log, newest first, paginated, with filters.
* ``GET /usage/summary/``: aggregates grouped by provider, day, month, campaign, ... (same filters).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any, ClassVar

from django.db.models import Exists, OuterRef
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.permissions import ClientScopedViewSet, request_roles
from apps.core.roles import Level, role_allows
from apps.core.schema import error_responses
from apps.core.tenancy import is_global_admin
from apps.integrations import reporting
from apps.integrations.models import ProviderCall, RawPayload

from .usage_serializers import (
    ProviderCallSerializer,
    UsageQuerySerializer,
    UsageSummaryQuerySerializer,
    UsageSummarySerializer,
)

TAGS = ["integrations"]
_NEEDS = (
    "Needs the `EDIT` level: manager and admin. Others get 403; clients you are not a member of "
    "give 404 (their rows are simply absent)."
)
FILTER_FIELDS = {
    "client": "client_id",
    "connection": "connection_id",
    "campaign": "campaign_id",
    "job": "job_id",
    "category": "category",
    "provider": "provider",
    "operation": "operation",
    "status": "status",
    "cache_hit": "cache_hit",
}


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        operation_id="integration_usage_list",
        summary="List provider calls",
        description="One row per provider call, cache hit or budget refusal (never a request or "
        "response body). Newest first. " + _NEEDS,
        parameters=[UsageQuerySerializer],
        responses={
            200: ProviderCallSerializer(many=True),
            **error_responses(400, 401, 403, 429, 500),
        },
    ),
)
class UsageViewSet(mixins.ListModelMixin, ClientScopedViewSet):
    queryset = ProviderCall.objects.all()
    serializer_class = ProviderCallSerializer
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ("created_at", "cost", "credits", "latency_ms")
    ordering = ("-created_at", "-id")

    action_levels: ClassVar[dict[str, Level]] = {"list": Level.EDIT, "summary": Level.EDIT}

    def _filtered(self, queryset: Any, query: Any) -> Any:
        if not is_global_admin(self.request.user):
            allowed = [
                client_id
                for client_id, role in request_roles(self.request).items()
                if role_allows(role, Level.EDIT)
            ]
            queryset = queryset.filter(client_id__in=allowed)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        for name, column in FILTER_FIELDS.items():
            if name in params:
                queryset = queryset.filter(**{column: params[name]})
        if "date_from" in params:
            start = datetime.combine(params["date_from"], time.min, tzinfo=UTC)
            queryset = queryset.filter(created_at__gte=start)
        if "date_to" in params:
            end = datetime.combine(params["date_to"] + timedelta(days=1), time.min, tzinfo=UTC)
            queryset = queryset.filter(created_at__lt=end)
        if (
            "date_from" in params
            and "date_to" in params
            and params["date_from"] > params["date_to"]
        ):
            raise ValidationError({"date_to": "Must not be before date_from."})
        return queryset

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        queryset = self._filtered(queryset, UsageQuerySerializer(data=self.request.query_params))
        if self.action == "list":
            raw = RawPayload.objects.filter(call=OuterRef("pk"))
            queryset = queryset.annotate(has_raw_payload=Exists(raw))
        return queryset

    @extend_schema(
        tags=TAGS,
        operation_id="integration_usage_summary",
        summary="Summarize provider usage",
        description="Totals of the logged rows grouped by `group_by` (default `provider`). Money "
        "is never added across currencies: each row has its `currency`. Credits are the "
        "provider's own unit. " + _NEEDS,
        parameters=[UsageSummaryQuerySerializer],
        responses={200: UsageSummarySerializer, **error_responses(400, 401, 403, 429, 500)},
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        group_by = (
            tuple(
                dict.fromkeys(
                    g.strip()
                    for g in str(request.query_params.get("group_by", "")).split(",")
                    if g.strip()
                )
            )
            or reporting.DEFAULT_GROUP
        )
        unknown = [g for g in group_by if g not in reporting.GROUPS]
        if unknown:
            raise ValidationError({"group_by": f"Unknown grouping: {', '.join(unknown)}."})
        queryset = self.get_queryset()
        data = {
            "group_by": list(group_by),
            "totals": reporting.totals(queryset),
            "results": reporting.summarize(queryset, group_by),
        }
        return Response(UsageSummarySerializer(data).data)
