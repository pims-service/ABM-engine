"""Routes of the integrations API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import ConnectionViewSet

router = SimpleRouter()
router.register("integrations/connections", ConnectionViewSet, basename="integration-connection")

urlpatterns = [path("", include(router.urls))]
