from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from .schema import error_responses


class ApiLinksSerializer(serializers.Serializer[dict[str, str]]):
    self = serializers.URLField(help_text="Absolute URL of this endpoint.")


class ApiRootSerializer(serializers.Serializer[dict[str, Any]]):
    """Body of `GET /api/v1/` (documentation only)."""

    name = serializers.CharField()
    version = serializers.CharField()
    links = ApiLinksSerializer()


class ApiRootView(APIView):
    """Public entry point of the v1 API."""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(
        tags=["meta"],
        operation_id="api_root",
        summary="API name, version and self link",
        responses={200: ApiRootSerializer, **error_responses(429, 500)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(
            {
                "name": "ABM Engine API",
                "version": request.version,
                "links": {"self": reverse("v1:api-root", request=request)},
            }
        )
