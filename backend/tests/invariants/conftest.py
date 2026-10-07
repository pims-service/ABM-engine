"""Fixtures and the ``invariants`` marker for this package (issue #53).

Every test collected under ``tests/invariants`` gets ``pytest.mark.invariants`` automatically, so
``pytest -m invariants`` always runs the whole group, whatever file a new test lands in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from apps.campaigns.models import Client
from tests.factories import make_client
from tests.invariants.registry import build_client_rows

HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.invariants)


@dataclass
class TwoClients:
    """A full set of rows (one per model) in each of two clients."""

    a: Client
    b: Client
    rows_a: dict[str, Any]
    rows_b: dict[str, Any]


@pytest.fixture
def two_clients(db: None) -> TwoClients:
    a, b = make_client(name="Invariant A"), make_client(name="Invariant B")
    return TwoClients(a=a, b=b, rows_a=build_client_rows(a), rows_b=build_client_rows(b))
