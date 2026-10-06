"""Migration hygiene: models and migrations must not drift; extensions come from migrations."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection


@pytest.mark.django_db
def test_no_missing_migrations():
    """Fails when a model change has no migration (`makemigrations --check` exits non-zero)."""
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL")
@pytest.mark.django_db
@pytest.mark.parametrize("extension", ["pgcrypto", "pg_trgm"])
def test_extensions_installed_by_migrations(extension):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", [extension])
        assert cursor.fetchone() is not None


@pytest.mark.django_db
def test_extension_migration_is_applied_cleanly():
    """Runs on SQLite too, where the extension operations must be no-ops."""
    call_command("migrate", "core", verbosity=0)
