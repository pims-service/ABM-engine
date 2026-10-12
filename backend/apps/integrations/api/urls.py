"""Routes of the integrations API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .usage_views import UsageViewSet
from .views import ConnectionViewSet

router = SimpleRouter()
router.register("integrations/connections", ConnectionViewSet, basename="integration-connection")
router.register("integrations/usage", UsageViewSet, basename="integration-usage")

urlpatterns = [path("", include(router.urls))]
