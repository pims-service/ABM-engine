"""Routes of the campaign API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .campaigns import CampaignViewSet, ClientCampaignViewSet

router = SimpleRouter()
router.register("campaigns", CampaignViewSet, basename="campaign")

urlpatterns = [
    path(
        "clients/<uuid:client_pk>/campaigns/",
        ClientCampaignViewSet.as_view({"get": "list", "post": "create"}),
        name="client-campaigns",
    ),
    path("", include(router.urls)),
]
