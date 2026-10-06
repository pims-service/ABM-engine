"""Who may see which client. The single place the tenancy rule is decided.

``TenantQuerySet.for_user`` (``apps.core.base``) asks :func:`accessible_client_ids`. Rules:

* Anonymous and inactive users get nothing (fails closed).
* Global admins (active superusers) get every client (``None``).
* Everyone else gets the clients where they hold a non-archived ``ClientMembership``.

Roles per client come from :func:`client_roles`; the DRF layer is ``apps.core.permissions``.
"""

from __future__ import annotations

import uuid
from typing import Any

from .roles import Level, role_allows


def is_global_admin(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active and user.is_superuser)


def _is_live(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def client_roles(user: Any) -> dict[uuid.UUID, str]:
    """``{client_id: role}`` for the user's active memberships. One query.

    Global admins are not limited by roles: use :func:`has_client_level`, which handles that.
    """
    if not _is_live(user):
        return {}
    from apps.campaigns.models import ClientMembership  # local: models import this module

    rows = ClientMembership.objects.filter(user_id=user.pk, archived_at__isnull=True).values_list(
        "client_id", "role"
    )
    return dict(rows)


def accessible_client_ids(user: Any) -> set[uuid.UUID] | None:
    """Client ids ``user`` may see, or ``None`` meaning every client (global admin)."""
    if not _is_live(user):
        return set()
    if is_global_admin(user):
        return None
    return set(client_roles(user))


def role_in_client(user: Any, client_id: Any) -> str | None:
    """The user's membership role for one client, or ``None`` (also for global admins)."""
    if not _is_live(user) or client_id is None:
        return None
    from apps.campaigns.models import ClientMembership

    return (
        ClientMembership.objects.filter(
            user_id=user.pk, client_id=client_id, archived_at__isnull=True
        )
        .values_list("role", flat=True)
        .first()
    )


def has_client_level(user: Any, client_id: Any, level: Level) -> bool:
    """Global admins pass everything; others need a membership role that reaches ``level``."""
    if is_global_admin(user):
        return True
    return role_allows(role_in_client(user, client_id), level)
