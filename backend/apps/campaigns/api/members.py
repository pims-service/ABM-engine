"""Who is on a client's team, and as what (Brief section 3: multiple clients, separate access).

Client admins list their team, add people, change roles and remove access. A person who has no
account yet is created with the password the admin types (they can change it themselves); there
is no email sending in V1. The last admin of a client can never be removed or demoted.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.campaigns import memberships
from apps.campaigns.models import Client, ClientMembership
from apps.core.roles import Role


class MemberSerializer(serializers.Serializer[Any]):
    user_id = serializers.UUIDField()
    email = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    role = serializers.CharField(help_text="viewer, reviewer, manager or admin.")
    is_you = serializers.BooleanField()


class AddMemberSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=Role.choices)
    name = serializers.CharField(required=False, allow_blank=True, max_length=150, default="")
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        style={"input_type": "password"},
        help_text="Only for a person who has no account yet. They can change it after signing in.",
    )


class SetMemberRoleSerializer(serializers.Serializer[Any]):
    user_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=Role.choices)


class RemoveMemberSerializer(serializers.Serializer[Any]):
    user_id = serializers.UUIDField()


def _body(membership: ClientMembership, me: Any) -> dict[str, Any]:
    user = membership.user
    return {
        "user_id": user.pk,
        "email": user.email,
        "name": user.name,
        "role": membership.role,
        "is_you": user.pk == getattr(me, "pk", None),
    }


def list_members(client: Client, me: Any) -> list[dict[str, Any]]:
    rows = ClientMembership.objects.active().filter(client=client).select_related("user")
    return [_body(m, me) for m in sorted(rows, key=lambda m: m.user.email.lower())]


def _membership(client: Client, user_id: Any) -> ClientMembership:
    row = (
        ClientMembership.objects.active()
        .filter(client=client, user_id=user_id)
        .select_related("user")
        .first()
    )
    if row is None:
        raise ValidationError({"user_id": ["This person is not on the team."]})
    return row


def _keep_an_admin(client: Client, leaving: ClientMembership) -> None:
    others = (
        ClientMembership.objects.active()
        .filter(client=client, role=Role.ADMIN)
        .exclude(pk=leaving.pk)
        .exists()
    )
    if leaving.role == Role.ADMIN and not others:
        raise ValidationError(
            {
                "non_field_errors": [
                    "A client needs at least one admin. Make someone else admin first."
                ]
            }
        )


def _django(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "error_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"non_field_errors": exc.messages})


def add_member(client: Client, data: dict[str, Any], actor: User) -> dict[str, Any]:
    """Give a person a role; create their account first when they have none."""
    email = User.objects.normalize_email(data["email"]).lower()
    user = User.objects.filter(email=email).first()
    try:
        if user is None:
            password = data.get("password") or ""
            if not password:
                raise ValidationError(
                    {"password": ["This person has no account yet: choose a password for them."]}
                )
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                raise ValidationError({"password": exc.messages}) from exc
            user = User.objects.create_user(email, password, name=data.get("name", ""))
        existing = ClientMembership.objects.active().filter(client=client, user=user).first()
        if existing is not None and existing.role == Role.ADMIN and data["role"] != Role.ADMIN:
            _keep_an_admin(client, existing)
        membership = memberships.grant_membership(client, user, data["role"], actor=actor)
    except DjangoValidationError as exc:
        raise _django(exc) from exc
    membership.user = user
    return _body(membership, actor)


def set_role(client: Client, user_id: Any, role: str, actor: User) -> dict[str, Any]:
    row = _membership(client, user_id)
    if role != Role.ADMIN:
        _keep_an_admin(client, row)
    try:
        updated = memberships.change_role(client, row.user, role, actor=actor)
    except DjangoValidationError as exc:
        raise _django(exc) from exc
    updated.user = row.user
    return _body(updated, actor)


def remove(client: Client, user_id: Any, actor: User) -> None:
    row = _membership(client, user_id)
    _keep_an_admin(client, row)
    try:
        memberships.revoke_membership(client, row.user, actor=actor)
    except DjangoValidationError as exc:
        raise _django(exc) from exc
