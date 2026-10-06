from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView


class ApiRootView(APIView):
    """Public entry point of the v1 API."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request, *args, **kwargs):
        return Response(
            {
                "name": "ABM Engine API",
                "version": request.version,
                "links": {"self": reverse("v1:api-root", request=request)},
            }
        )
