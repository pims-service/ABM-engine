"""Worked example of the permission pattern (issue #46). Test-only: the real endpoints are #47/#48.

Copy this shape for a new endpoint; ``docs/permissions.md`` explains each line. URLconf for the
tests is ``tests.permissions_demo`` (``pytest.mark.urls``).
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.urls import include, path
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import User
from apps.campaigns.models import Campaign, Client
from apps.campaigns.services import create_campaign, create_profile_version
from apps.core.permissions import ClientScopedModelViewSet, ClientScopedReadOnlyModelViewSet
from apps.core.roles import Level


class CampaignSerializer(serializers.ModelSerializer[Campaign]):
    class Meta:
        model = Campaign
        fields = ("id", "client", "name", "status")
        read_only_fields = ("id", "client", "status")


class ClientSerializer(serializers.ModelSerializer[Client]):
    class Meta:
        model = Client
        fields = ("id", "name")


class ClientViewSet(ClientScopedReadOnlyModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer


class CampaignViewSet(ClientScopedModelViewSet):
    queryset = Campaign.objects.select_related("client").all()
    serializer_class = CampaignSerializer
    # Anything not listed: GET needs READ, every write needs MANAGE (fail closed).
    action_levels: ClassVar[dict[str, Level]] = {
        "create": Level.EDIT,
        "partial_update": Level.EDIT,
        "update": Level.EDIT,
        "rules": Level.EDIT,  # new profile version = editing campaign rules
        "decide": Level.DECIDE,  # record a human decision
        "destroy": Level.MANAGE,
    }

    def perform_create(self, serializer: Any) -> None:
        # The client comes from the body, so it is resolved through the scoped lookup (404/403).
        user = cast(User, self.request.user)
        client = self.resolve_client(dict(self.request.data).get("client"), Level.EDIT)
        campaign = create_campaign(
            client, serializer.validated_data["name"], {"offer": "Demo"}, user
        )
        serializer.instance = campaign

    def perform_destroy(self, instance: Campaign) -> None:
        instance.archive()

    @action(detail=True, methods=["post"])
    def rules(self, request: Request, pk: str | None = None) -> Response:
        campaign = self.get_object()
        create_profile_version(campaign, dict(request.data), cast(User, request.user))
        return Response({"version": campaign.profiles.count()})

    @action(detail=True, methods=["post"])
    def decide(self, request: Request, pk: str | None = None) -> Response:
        self.get_object()  # scoping and role check happen here
        return Response({"recorded": True})


router = DefaultRouter()
router.register("campaigns", CampaignViewSet, basename="campaign")
router.register("clients", ClientViewSet, basename="client")

urlpatterns = [path("v1/", include((router.urls, "v1")))]
