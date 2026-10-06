from django.apps import apps
from django.conf import settings
from rest_framework.test import APIClient

LOCAL_APP_LABELS = {"accounts", "campaigns", "companies", "research", "integrations", "ai", "core"}


def test_settings_load():
    assert settings.SETTINGS_MODULE == "config.settings.test"
    assert settings.SECRET_KEY
    assert not settings.DEBUG


def test_local_apps_registered():
    assert LOCAL_APP_LABELS <= {c.label for c in apps.get_app_configs()}


def test_api_root():
    response = APIClient().get("/api/v1/")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "v1"
    assert body["links"]["self"].endswith("/api/v1/")
