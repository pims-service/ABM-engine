from django.contrib import admin
from django.urls import include, path

from apps.core.health import HealthzView, ReadyzView

# JSON error envelope for unknown /api/ routes and unhandled errors outside DRF views.
handler404 = "apps.core.exceptions.not_found_view"
handler500 = "apps.core.exceptions.server_error_view"

urlpatterns = [
    # Probes live at the root, not under /api/v1/: infra checks should not depend on API versions.
    path("healthz", HealthzView.as_view(), name="healthz"),
    path("readyz", ReadyzView.as_view(), name="readyz"),
    path("admin/", admin.site.urls),
    path("api/v1/", include(("apps.core.urls", "v1"), namespace="v1")),
]
