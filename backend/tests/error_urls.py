"""URLconf with views that raise each kind of error, used by the exception handler tests."""

from __future__ import annotations

from typing import Any

from django.http import Http404
from django.urls import path
from rest_framework import serializers
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

handler404 = "apps.core.exceptions.not_found_view"
handler500 = "apps.core.exceptions.server_error_view"


class _Payload(serializers.Serializer):
    name = serializers.CharField()
    age = serializers.IntegerField(min_value=0)


class _Open(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()


class ValidationView(_Open):
    def post(self, request: Request) -> Response:
        serializer = _Payload(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class BasicAuthView(APIView):
    authentication_classes = (BasicAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        return Response({})


class SessionAuthView(APIView):
    """Uses project defaults: unauthenticated callers are rejected."""

    def get(self, request: Request) -> Response:
        return Response({})


class ForbiddenView(_Open):
    def get(self, request: Request) -> Response:
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("You may not do that.")


class DjangoForbiddenView(_Open):
    def get(self, request: Request) -> Response:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied


class NotFoundView(_Open):
    def get(self, request: Request) -> Response:
        from rest_framework.exceptions import NotFound

        raise NotFound("No such company.")


class Django404View(_Open):
    def get(self, request: Request) -> Response:
        raise Http404


class _AlwaysThrottle(SimpleRateThrottle):
    scope = "burst"
    THROTTLE_RATES = {"burst": "1/min"}  # noqa: RUF012

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return None

    def allow_request(self, request: Any, view: Any) -> bool:
        return False

    def wait(self) -> float | None:
        return 30.0


class ThrottledView(_Open):
    throttle_classes = (_AlwaysThrottle,)

    def get(self, request: Request) -> Response:
        return Response({})


class TeapotView(_Open):
    """A custom APIException falls back to its ``default_code``."""

    class Teapot(APIException):
        status_code = 418
        default_detail = "I am a teapot."
        default_code = "teapot"

    def get(self, request: Request) -> Response:
        raise self.Teapot


class BoomView(_Open):
    def get(self, request: Request) -> Response:
        raise ValueError("db password=hunter2 at /srv/app/secret_module.py")


class OnlyPostView(_Open):
    def post(self, request: Request) -> Response:
        return Response({})


urlpatterns = [
    path("api/v1/validation/", ValidationView.as_view()),
    path("api/v1/basic-auth/", BasicAuthView.as_view()),
    path("api/v1/session-auth/", SessionAuthView.as_view()),
    path("api/v1/forbidden/", ForbiddenView.as_view()),
    path("api/v1/django-forbidden/", DjangoForbiddenView.as_view()),
    path("api/v1/not-found/", NotFoundView.as_view()),
    path("api/v1/django-404/", Django404View.as_view()),
    path("api/v1/throttled/", ThrottledView.as_view()),
    path("api/v1/teapot/", TeapotView.as_view()),
    path("api/v1/boom/", BoomView.as_view()),
    path("api/v1/only-post/", OnlyPostView.as_view()),
]
