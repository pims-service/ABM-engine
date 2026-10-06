from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

MODULE = "apps.core.management.commands.reset_local_db"
POSTGRES = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "abm_dev",
    "USER": "u",
    "PASSWORD": "p",
    "HOST": "localhost",
    "PORT": "5432",
}


@pytest.fixture
def cfg():
    """Stand-in for the command's `settings`: a local Postgres with DEBUG on. Mutate per test."""
    stub = SimpleNamespace(DEBUG=True, DATABASES={"default": dict(POSTGRES)})
    with patch(f"{MODULE}.settings", stub):
        yield stub


def test_refuses_non_postgres(cfg):
    cfg.DATABASES["default"]["ENGINE"] = "django.db.backends.sqlite3"
    with pytest.raises(CommandError, match="PostgreSQL"):
        call_command("reset_local_db", "--yes")


def test_refuses_when_debug_off(cfg):
    cfg.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("reset_local_db", "--yes")


def test_refuses_remote_host(cfg):
    cfg.DATABASES["default"]["HOST"] = "db.example.com"
    with pytest.raises(CommandError, match="non-local"):
        call_command("reset_local_db", "--yes")


def test_aborts_on_wrong_confirmation(cfg):
    with patch("builtins.input", return_value="nope"), pytest.raises(CommandError, match="Aborted"):
        call_command("reset_local_db")


def test_drops_creates_and_migrates(cfg):
    conn = MagicMock()
    connect = MagicMock()
    connect.return_value.__enter__.return_value = conn
    with (
        patch(f"{MODULE}.psycopg.connect", connect),
        patch(f"{MODULE}.call_command") as migrate,
        patch(f"{MODULE}.connection"),
        patch("builtins.input", return_value="abm_dev"),
    ):
        call_command("reset_local_db")
    assert connect.call_args.kwargs["dbname"] == "postgres"
    statements = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("DROP DATABASE" in s for s in statements)
    assert any("CREATE DATABASE" in s for s in statements)
    migrate.assert_called_once()


def test_no_migrate_flag(cfg):
    with (
        patch(f"{MODULE}.psycopg.connect"),
        patch(f"{MODULE}.call_command") as migrate,
        patch(f"{MODULE}.connection"),
    ):
        call_command("reset_local_db", "--yes", "--no-migrate")
    migrate.assert_not_called()
