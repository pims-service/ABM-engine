"""Fixtures and the ``invariants`` marker for this package (issue #53).

Every test collected under ``tests/invariants`` gets ``pytest.mark.invariants`` automatically, so
``pytest -m invariants`` always runs the whole group, whatever file a new test lands in.

Building a full set of rows (and seeding) is slow on PostgreSQL, so the big fixtures are built
once per module inside a transaction that is rolled back at the end (``shared_db``). Each test
still runs in its own savepoint, so whatever a test changes is undone before the next one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from django.db import transaction

from apps.campaigns.models import Client
from tests.factories import make_client
from tests.invariants.registry import build_client_rows

HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.invariants)


@contextmanager
def shared_db(blocker: Any) -> Iterator[None]:
    """Open the database for a module-scoped fixture; everything it writes is rolled back."""
    with blocker.unblock():
        atomic = transaction.atomic()
        atomic.__enter__()
        try:
            yield
        finally:
            transaction.set_rollback(True)
            atomic.__exit__(None, None, None)


@dataclass
class TwoClients:
    """A full set of rows (one per model) in each of two clients."""

    a: Client
    b: Client
    rows_a: dict[str, Any]
    rows_b: dict[str, Any]


def _fresh(rows: dict[str, Any]) -> dict[str, Any]:
    """Re-read every row, so a test that edits an instance in memory cannot affect the next."""
    return {key: type(row)._base_manager.get(pk=row.pk) for key, row in rows.items()}


@pytest.fixture(scope="module")
def _two_clients_module(django_db_setup: None, django_db_blocker: Any) -> Iterator[TwoClients]:
    with shared_db(django_db_blocker):
        a, b = make_client(name="Invariant A"), make_client(name="Invariant B")
        yield TwoClients(a=a, b=b, rows_a=build_client_rows(a), rows_b=build_client_rows(b))


@pytest.fixture
def two_clients(_two_clients_module: TwoClients, db: None) -> TwoClients:
    shared = _two_clients_module
    rows_a, rows_b = _fresh(shared.rows_a), _fresh(shared.rows_b)
    return TwoClients(
        a=rows_a[Client._meta.label], b=rows_b[Client._meta.label], rows_a=rows_a, rows_b=rows_b
    )
