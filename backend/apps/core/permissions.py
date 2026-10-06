"""DRF building blocks for per-client access control (issue #46). Every endpoint uses these.

Quick use (full guide: ``docs/permissions.md``)::

    class CampaignViewSet(ClientScopedModelViewSet):
        queryset = Campaign.objects.all()          # a TenantQuerySet manager
        serializer_class = CampaignSerializer
        action_levels = {"update": Level.EDIT, "partial_update": Level.EDIT,
                         "decide": Level.DECIDE, "destroy": Level.MANAGE}

        def perform_create(self, serializer):
            client = self.resolve_client(self.request.data.get("client"), Level.EDIT)
            serializer.save(client=client)

What the pieces do:

* ``get_queryset`` always ends in ``.for_user(request.user)``, so rows of clients the user cannot
  see do not exist for them. Guessing an id gives **404, never 403** (the id cannot be probed).
* ``ClientRolePermission`` checks the role: viewer READ, reviewer DECIDE, manager EDIT, admin
  MANAGE (``apps.core.roles``). A user who can see the object but lacks the role gets 403.
* Global admins (active superusers) pass every check. Inactive and anonymous users get nothing.
* An action with no entry in ``action_levels`` needs READ if the method is safe (GET, HEAD,
  OPTIONS) and MANAGE otherwise, so a forgotten write action fails closed.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import Http404
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from .roles import Level, role_allows
from .tenancy import client_roles, is_global_admin


def _is_live(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def request_roles(request: Request) -> dict[Any, str]:
    """``{client_id: role}`` for the request's user, loaded once per request."""
    cached = getattr(request, "_client_roles", None)
    if cached is None:
        cached = client_roles(request.user)
        request._client_roles = cached  # type: ignore[attr-defined]
    return cached  # type: ignore[no-any-return,unused-ignore]


def required_level(view: APIView, request: Request) -> Level:
    """The level this request needs: ``action_levels`` first, then the safe/unsafe default."""
    getter = getattr(view, "get_required_level", None)
    if getter is None:
        raise ImproperlyConfigured(
            f"{type(view).__name__} uses ClientRolePermission without ClientScopedMixin."
        )
    return getter(request)  # type: ignore[no-any-return]


class ClientRolePermission(BasePermission):
    """Role check for views built on :class:`ClientScopedMixin`."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not _is_live(user):
            return False
        if is_global_admin(user):
            return True
        level = required_level(view, request)
        if level <= Level.READ:
            return True  # the queryset scoping decides what is visible
        # Detail routes are decided per object (404 if not theirs, 403 if the role is too low),
        # so a foreign id never answers differently from a missing one.
        lookup = getattr(view, "lookup_url_kwarg", None) or getattr(view, "lookup_field", "pk")
        if lookup in getattr(view, "kwargs", {}):
            return True
        # Collection writes (create): fail early unless some client gives the user this level.
        # The target client is then checked by resolve_client.
        return any(role_allows(role, level) for role in request_roles(request).values())

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        user = request.user
        if not _is_live(user):
            return False
        if is_global_admin(user):
            return True
        client_id = view.object_client_id(obj)  # type: ignore[attr-defined]
        role = request_roles(request).get(client_id)
        if role is None:
            raise Http404  # not their client: indistinguishable from "does not exist"
        return role_allows(role, required_level(view, request))


CLIENT_SCOPED_PERMISSIONS = [IsAuthenticated, ClientRolePermission]


class ClientScopedMixin:
    """Put first in the bases of every view that serves tenant data."""

    # Using the mixin on a bare APIView? Set ``permission_classes = CLIENT_SCOPED_PERMISSIONS``.

    #: ``{action_name: Level}``. Actions are DRF viewset actions (``list``, ``create``,
    #: ``partial_update``, custom ``@action`` names) or lower-case HTTP methods for APIView.
    action_levels: ClassVar[dict[str, Level]] = {}

    def get_required_level(self, request: Request) -> Level:
        action = getattr(self, "action", None) or request.method.lower()  # type: ignore[union-attr]
        if action in self.action_levels:
            return self.action_levels[action]
        return Level.READ if request.method in SAFE_METHODS else Level.MANAGE

    def object_client_id(self, obj: Any) -> Any:
        """The client an object belongs to. ``Client`` itself is its own tenant."""
        return obj.pk if type(obj).__name__ == "Client" else obj.client_id

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()  # type: ignore[misc]
        for_user = getattr(queryset, "for_user", None)
        if for_user is None:
            raise ImproperlyConfigured(
                f"{type(self).__name__}.queryset must come from a TenantQuerySet manager "
                "(it has no for_user())."
            )
        return for_user(self.request.user)  # type: ignore[attr-defined]

    def resolve_client(self, client_id: Any, level: Level = Level.EDIT) -> Any:
        """Load the client a write targets (create, move, nested resources).

        404 when the user cannot see that client (or it does not exist), 403 when they can see it
        but their role is below ``level``. Never trust a client id from a request body without it.
        """
        from apps.campaigns.models import Client

        try:
            client = Client.objects.for_user(self.request.user).get(pk=client_id)  # type: ignore[attr-defined]
        except (Client.DoesNotExist, ValidationError, ValueError, TypeError) as exc:
            raise Http404 from exc  # unknown, malformed or someone else's id: all "not found"
        if not is_global_admin(self.request.user):  # type: ignore[attr-defined]
            role = request_roles(self.request).get(client.pk)  # type: ignore[attr-defined]
            if not role_allows(role, level):
                raise PermissionDenied("Your role does not allow this action.")
        return client


class ClientScopedViewSet(ClientScopedMixin, viewsets.GenericViewSet):  # type: ignore[type-arg]
    """GenericViewSet with client scoping and role checks."""

    permission_classes = CLIENT_SCOPED_PERMISSIONS


class ClientScopedModelViewSet(ClientScopedMixin, viewsets.ModelViewSet):  # type: ignore[type-arg]
    """ModelViewSet with client scoping and role checks. The default for CRUD endpoints."""

    permission_classes = CLIENT_SCOPED_PERMISSIONS


class ClientScopedReadOnlyModelViewSet(ClientScopedMixin, viewsets.ReadOnlyModelViewSet):  # type: ignore[type-arg]
    """Read-only list/retrieve with client scoping."""

    permission_classes = CLIENT_SCOPED_PERMISSIONS
