from django.contrib import admin
from django.urls import include, path

# JSON error envelope for unknown /api/ routes and unhandled errors outside DRF views.
handler404 = "apps.core.exceptions.not_found_view"
handler500 = "apps.core.exceptions.server_error_view"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(("apps.core.urls", "v1"), namespace="v1")),
]
