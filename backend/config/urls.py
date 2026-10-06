from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.core.health import HealthzView, ReadyzView
from apps.core.schema import docs_enabled_only

# JSON error envelope for unknown /api/ routes and unhandled errors outside DRF views.
handler404 = "apps.core.exceptions.not_found_view"
handler500 = "apps.core.exceptions.server_error_view"

urlpatterns = [
    # Probes live at the root, not under /api/v1/: infra checks should not depend on API versions.
    path("healthz", HealthzView.as_view(), name="healthz"),
    path("readyz", ReadyzView.as_view(), name="readyz"),
    path("admin/", admin.site.urls),
    # OpenAPI schema and docs UIs: 404 unless settings.API_DOCS_ENABLED (dev only by default).
    path("api/v1/schema/", docs_enabled_only(SpectacularAPIView.as_view()), name="schema"),
    path(
        "api/v1/docs/",
        docs_enabled_only(SpectacularSwaggerView.as_view(url_name="schema")),
        name="docs",
    ),
    path(
        "api/v1/redoc/",
        docs_enabled_only(SpectacularRedocView.as_view(url_name="schema")),
        name="redoc",
    ),
    path("api/v1/", include(("apps.core.urls", "v1"), namespace="v1")),
]
