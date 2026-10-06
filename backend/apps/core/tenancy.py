"""Who may see which client. The single place the tenancy rule is decided.

``TenantQuerySet.for_user`` (``apps.core.models``) asks :func:`accessible_client_ids`. Issue #46
(ClientMembership and roles) replaces the body so that non-admin users get the clients they have
a membership in. Until then only global admins (superusers) see data and everyone else sees
nothing: the rule fails closed.
"""

from __future__ import annotations

import uuid
from typing import Any


def is_global_admin(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active and user.is_superuser)


def accessible_client_ids(user: Any) -> set[uuid.UUID] | None:
    """Client ids ``user`` may see, or ``None`` meaning every client (global admin)."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return set()
    if is_global_admin(user):
        return None
    return set()  # TODO(#46): ids from the user's ClientMembership rows.
