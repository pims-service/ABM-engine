"""Example: view layer. Shows public vs authenticated access using the shared client fixtures."""

import pytest
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.accounts.models import User


class PublicView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        return Response({"ok": True})


class WhoAmIView(APIView):
    """Protected by the project default permission (IsAuthenticated)."""

    def get(self, request: Request) -> Response:
        return Response({"email": request.user.get_username()})


# `pytest.mark.urls` swaps the URLconf for this module, so no real app needs a protected route.
urlpatterns = [
    path("", PublicView.as_view(), name="public"),
    path("whoami/", WhoAmIView.as_view(), name="whoami"),
]
pytestmark = [pytest.mark.api, pytest.mark.urls(__name__)]


def test_public_endpoint_needs_no_auth(api_client: APIClient):
    assert api_client.get("/").status_code == 200


def test_protected_endpoint_rejects_anonymous(api_client: APIClient):
    assert api_client.get("/whoami/").status_code == 401


def test_protected_endpoint_accepts_authenticated_user(auth_client: APIClient, user: User):
    response = auth_client.get("/whoami/")
    assert response.status_code == 200
    assert response.json() == {"email": user.email}
