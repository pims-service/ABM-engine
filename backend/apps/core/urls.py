from django.urls import include, path

from .views import ApiRootView

app_name = "v1"

urlpatterns = [
    path("", ApiRootView.as_view(), name="api-root"),
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.campaigns.api.client_urls")),
    path("", include("apps.campaigns.api.campaign_urls")),
    path("", include("apps.imports.api.csv_urls")),
    path("", include("apps.companies.api.urls")),
    path("", include("apps.integrations.api.urls")),
]
