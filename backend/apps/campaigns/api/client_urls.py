"""Routes of the client API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .clients import ClientViewSet

router = SimpleRouter()
router.register("clients", ClientViewSet, basename="client")

urlpatterns = [path("", include(router.urls))]
