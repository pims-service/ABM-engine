"""Routes of the provider import API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import path

from .views import CampaignProviderViewSet, ProviderImportViewSet, ProviderViewSet

urlpatterns = [
    path("providers/", ProviderViewSet.as_view({"get": "list"}), name="providers"),
    path(
        "campaigns/<uuid:pk>/provider-search/",
        CampaignProviderViewSet.as_view({"post": "search"}, throttle_scope="provider_search"),
        name="campaign-provider-search",
    ),
    path(
        "campaigns/<uuid:pk>/provider-import/",
        CampaignProviderViewSet.as_view(
            {"post": "import_selected"}, throttle_scope="provider_import"
        ),
        name="campaign-provider-import",
    ),
    path(
        "provider-imports/<uuid:pk>/",
        ProviderImportViewSet.as_view({"get": "retrieve"}),
        name="provider-import-detail",
    ),
]
