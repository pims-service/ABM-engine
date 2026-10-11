"""Django admin for integration connections (issue #76): read-only, superuser-only, masked."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.test import Client as DjangoClient
from django.urls import reverse

from apps.integrations.admin import IntegrationConnectionAdmin
from apps.integrations.models import IntegrationConnection
from tests.factories import make_user
from tests.factories_integrations import FAKE_SECRET, make_connection

pytestmark = pytest.mark.django_db


def _login(**flags: bool) -> DjangoClient:
    user = make_user(is_staff=True, **flags)
    browser = DjangoClient()
    browser.force_login(user)
    return browser


def _urls(conn: IntegrationConnection) -> tuple[str, str]:
    return (
        reverse("admin:integrations_integrationconnection_changelist"),
        reverse("admin:integrations_integrationconnection_change", args=[conn.pk]),
    )


def test_a_superuser_sees_pages_without_any_secret() -> None:
    conn = make_connection()
    browser = _login(is_superuser=True)
    for url in _urls(conn):
        response = browser.get(url)
        assert response.status_code == 200
        text = response.content.decode()
        assert FAKE_SECRET not in text
        assert "gAAAA" not in text  # not even the ciphertext
    detail = browser.get(_urls(conn)[1]).content.decode()
    assert f"****{FAKE_SECRET[-4:]}" in detail  # the masked hint


def test_the_credentials_column_is_in_no_admin_view() -> None:
    model_admin = admin.site._registry[IntegrationConnection]
    assert isinstance(model_admin, IntegrationConnectionAdmin)
    assert "credentials" not in model_admin.fields
    assert "credentials" not in model_admin.list_display
    form = model_admin.get_form(None)  # type: ignore[arg-type]
    assert "credentials" not in form.base_fields


def test_staff_without_the_superuser_flag_cannot_open_it() -> None:
    conn = make_connection()
    browser = _login()  # staff, not superuser
    for url in _urls(conn):
        assert browser.get(url).status_code in (302, 403)


def test_nobody_can_add_change_or_delete_through_the_admin() -> None:
    conn = make_connection()
    browser = _login(is_superuser=True)
    changelist, change = _urls(conn)
    assert browser.get(reverse("admin:integrations_integrationconnection_add")).status_code == 403
    assert browser.post(change, {"name": "Hacked"}).status_code == 403
    assert (
        browser.get(
            reverse("admin:integrations_integrationconnection_delete", args=[conn.pk])
        ).status_code
        == 403
    )
    assert browser.post(
        changelist, {"action": "delete_selected", "_selected_action": [conn.pk]}
    ).status_code in (200, 302, 403)
    conn.refresh_from_db()
    assert conn.name != "Hacked"
