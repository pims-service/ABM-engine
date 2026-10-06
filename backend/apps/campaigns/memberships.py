"""Write path for client memberships: grant, change role, revoke.

Use these, never raw ``ClientMembership`` writes. ``actor`` is the user performing the change:
it must be a global admin or an admin of that client, otherwise ``PermissionDenied`` is raised
(the API layer should already have checked, this is the second lock). ``actor=None`` means a
trusted system caller (management command, seed, tests). Audit logging arrives with issue #44.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.core.roles import Level, Role
from apps.core.tenancy import has_client_level

from .models import Client, ClientMembership


def _check_actor(actor: User | None, client: Client) -> None:
    if actor is not None and not has_client_level(actor, client.pk, Level.MANAGE):
        raise PermissionDenied("Only a client admin can manage memberships.")


def _check_role(role: str) -> str:
    if role not in Role.values:
        raise ValidationError({"role": f"Unknown role {role!r}."})
    return role


@transaction.atomic
def grant_membership(
    client: Client, user: User, role: str, *, actor: User | None = None
) -> ClientMembership:
    """Give ``user`` ``role`` in ``client``. Restores an archived row; updates an active one."""
    _check_actor(actor, client)
    _check_role(role)
    if client.is_archived:
        raise ValidationError("Cannot grant access to an archived client.")
    membership = (
        ClientMembership.objects.select_for_update().filter(client=client, user=user).first()
    )
    if membership is None:
        return ClientMembership.objects.create(client=client, user=user, role=role)
    membership.role = role
    membership.archived_at = None
    membership.save(update_fields=["role", "archived_at", "updated_at"])
    return membership


@transaction.atomic
def change_role(
    client: Client, user: User, role: str, *, actor: User | None = None
) -> ClientMembership:
    """Change the role of an existing, active membership."""
    _check_actor(actor, client)
    _check_role(role)
    membership = _active(client, user)
    membership.role = role
    membership.save(update_fields=["role", "updated_at"])
    return membership


@transaction.atomic
def revoke_membership(client: Client, user: User, *, actor: User | None = None) -> ClientMembership:
    """Remove access. The row is archived (kept), and access ends immediately."""
    _check_actor(actor, client)
    membership = _active(client, user)
    membership.archive()
    return membership


def _active(client: Client, user: Any) -> ClientMembership:
    membership = (
        ClientMembership.objects.select_for_update()
        .filter(client=client, user=user, archived_at__isnull=True)
        .first()
    )
    if membership is None:
        raise ValidationError("This user is not an active member of the client.")
    return membership
