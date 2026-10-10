"""Reusable seeded-data fixtures (issue #52). Imported by ``tests/conftest.py``.

``seeded_world`` runs the real seeders (users, two clients with campaigns and memberships,
six companies with research, signals and contacts) and returns handles keyed as in
``apps.core.seed_sample`` (``world.users["skylight_admin"]``, ``world.clients["meridian"]``,
``world.campaigns[...]``, ``world.companies["tiqmo"]``). Sample users can log in with
``SEEDED_PASSWORD`` (a test-only value).
"""

from __future__ import annotations

import pytest

from apps.core.seed_sample import SEED_PASSWORD_ENV, SeededWorld, load_seeded_world
from apps.core.seeding import SEEDERS, SeedResult

SEEDED_PASSWORD = "seeded-test-password"  # pragma: allowlist secret


def run_all_seeders(environ: dict[str, str] | None = None) -> list[SeedResult]:
    """Run every registered seeder once and return their results."""
    env = {SEED_PASSWORD_ENV: SEEDED_PASSWORD, **(environ or {})}
    return [seeder(env) for seeder in SEEDERS]


@pytest.fixture
def seeded_world(db: None) -> SeededWorld:
    """Two clients (SkyLight, Meridian Labs) with sample users, campaigns and companies."""
    run_all_seeders()
    return load_seeded_world()
