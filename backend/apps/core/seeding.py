"""Idempotent building blocks for local development seed data.

Each seeder is a small function that can be run any number of times and leaves the database in
the same state. Register new ones in :data:`SEEDERS` as domain models arrive (M1+); the
``seed_dev_data`` command runs them in order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from django.contrib.auth import get_user_model


@dataclass(frozen=True)
class SeedResult:
    """What a seeder did, for the command's report."""

    name: str
    created: int = 0
    existing: int = 0
    detail: str = ""


Seeder = Callable[[Mapping[str, str]], SeedResult]


def seed_superuser(environ: Mapping[str, str]) -> SeedResult:
    """Create the dev superuser from ``DEV_SUPERUSER_*`` values; never touches an existing one.

    The password is only read from the environment, never hard-coded. If it is not set the
    seeder skips instead of creating an account with a known password.
    """
    password = environ.get("DEV_SUPERUSER_PASSWORD", "")
    if not password:
        return SeedResult("superuser", detail="skipped: DEV_SUPERUSER_PASSWORD is not set")
    username = environ.get("DEV_SUPERUSER_USERNAME", "admin")
    email = environ.get("DEV_SUPERUSER_EMAIL", "admin@example.com")

    user_model = get_user_model()
    if user_model._default_manager.filter(username=username).exists():
        return SeedResult("superuser", existing=1, detail=f"{username} already exists")
    user_model._default_manager.create_superuser(username=username, email=email, password=password)
    return SeedResult("superuser", created=1, detail=username)


# Sample domain data (companies, campaigns, ...) is added here once those models exist.
SEEDERS: tuple[Seeder, ...] = (seed_superuser,)
