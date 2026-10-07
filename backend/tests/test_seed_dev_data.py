from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def seed_env(monkeypatch, settings):
    settings.DEBUG = True
    monkeypatch.setenv("DEV_SUPERUSER_EMAIL", "DevAdmin@example.com")
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", "from-env-only")


def run_seed(*args: str) -> str:
    out = StringIO()
    call_command("seed_dev_data", *args, stdout=out)
    return out.getvalue()


def test_creates_superuser_from_env(seed_env):
    output = run_seed()
    user = User.objects.get(email="devadmin@example.com")
    assert user.is_superuser
    assert user.is_staff
    assert user.is_active
    assert user.check_password("from-env-only")
    assert "created=1" in output


def test_is_idempotent(seed_env):
    run_seed()
    output = run_seed()
    assert User.objects.filter(email="devadmin@example.com").count() == 1
    assert "created=0 existing=1" in output


def test_existing_user_password_is_not_overwritten(seed_env, monkeypatch):
    run_seed()
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", "a-different-value")
    run_seed()
    assert User.objects.get(email="devadmin@example.com").check_password("from-env-only")


def test_skips_superuser_without_password(seed_env, monkeypatch):
    monkeypatch.delenv("DEV_SUPERUSER_PASSWORD")
    output = run_seed()
    assert not User.objects.filter(is_superuser=True).exists()
    assert "skipped" in output


def test_default_email(seed_env, monkeypatch):
    monkeypatch.delenv("DEV_SUPERUSER_EMAIL")
    run_seed()
    assert User.objects.get(email="admin@example.com").is_superuser


def test_refuses_when_debug_off(settings, monkeypatch):
    settings.DEBUG = False
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", "from-env-only")
    with pytest.raises(CommandError, match="DEBUG"):
        run_seed()
    assert not User.objects.exists()


def test_force_overrides_debug_guard(settings, monkeypatch):
    settings.DEBUG = False
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", "from-env-only")
    run_seed("--force")
    assert User.objects.filter(email="admin@example.com").exists()
