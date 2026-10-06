"""Client roles and the permission levels they grant (issue #46).

Pure data, no model imports, so models, services and DRF classes can all import it. The
matrix is documented in ``docs/permissions.md``; change both together.
"""

from __future__ import annotations

import enum

from django.db import models


class Role(models.TextChoices):
    """A user's role inside one client (``ClientMembership.role``)."""

    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    REVIEWER = "reviewer", "Reviewer"
    VIEWER = "viewer", "Viewer"


class Level(enum.IntEnum):
    """What an action needs. Each level includes the ones below it."""

    READ = 10  # see the client's data
    DECIDE = 20  # record human decisions (approve / reject / defer)
    EDIT = 30  # edit campaign rules, companies, contacts, campaigns
    MANAGE = 40  # manage the client's memberships, archive the client


ROLE_LEVEL: dict[str, Level] = {
    Role.VIEWER: Level.READ,
    Role.REVIEWER: Level.DECIDE,
    Role.MANAGER: Level.EDIT,
    Role.ADMIN: Level.MANAGE,
}


def role_allows(role: str | None, level: Level) -> bool:
    """True when ``role`` (a ``Role`` value, or ``None`` for no membership) reaches ``level``."""
    if role is None:
        return False
    granted = ROLE_LEVEL.get(role)
    return granted is not None and granted >= level
