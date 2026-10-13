"""Routes of the company API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .companies import CampaignCompanyViewSet, CompanyViewSet

router = SimpleRouter()
router.register("companies", CompanyViewSet, basename="company")

urlpatterns = [
    path(
        "campaigns/<uuid:campaign_pk>/companies/",
        CampaignCompanyViewSet.as_view({"get": "list", "post": "create"}),
        name="campaign-companies",
    ),
    path("", include(router.urls)),
]
