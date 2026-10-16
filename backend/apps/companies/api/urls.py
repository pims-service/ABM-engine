"""Routes of the company API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .accounts import AccountViewSet
from .companies import CampaignCompanyViewSet, CompanyViewSet

router = SimpleRouter()
router.register("companies", CompanyViewSet, basename="company")

urlpatterns = [
    path(
        "campaigns/<uuid:campaign_pk>/companies/",
        CampaignCompanyViewSet.as_view({"get": "list", "post": "create"}),
        name="campaign-companies",
    ),
    path(
        "campaigns/<uuid:campaign_pk>/accounts/",
        AccountViewSet.as_view({"get": "list"}),
        name="campaign-accounts",
    ),
    path(
        "campaigns/<uuid:campaign_pk>/accounts/summary/",
        AccountViewSet.as_view({"get": "summary"}),
        name="campaign-accounts-summary",
    ),
    path(
        "campaigns/<uuid:campaign_pk>/accounts/analyze/",
        AccountViewSet.as_view({"post": "analyze"}),
        name="campaign-accounts-analyze",
    ),
    path(
        "campaigns/<uuid:campaign_pk>/accounts/decide/",
        AccountViewSet.as_view({"post": "decide"}),
        name="campaign-accounts-decide",
    ),
    path(
        "campaigns/<uuid:campaign_pk>/accounts/export/",
        AccountViewSet.as_view({"get": "export"}),
        name="campaign-accounts-export",
    ),
    path(
        "campaigns/<uuid:campaign_pk>/accounts/push-to-crm/",
        AccountViewSet.as_view({"post": "push_to_crm"}),
        name="campaign-accounts-push-to-crm",
    ),
    path("", include(router.urls)),
]
