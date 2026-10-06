"""Django admin for tenancy models.

Rows are archived, never deleted, so nobody gets a delete button. Campaigns are created through
``services.create_campaign`` (the admin cannot build the version 1 profile) and their rules only
change through ``services.create_profile_version``; profile versions are strictly read-only.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from .memberships import change_role, grant_membership, revoke_membership
from .models import Campaign, CampaignProfile, Client, ClientMembership


class NoDeleteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Client)
class ClientAdmin(NoDeleteAdmin):
    list_display = ("name", "status", "created_by", "created_at", "archived_at")
    list_filter = ("status",)
    search_fields = ("name",)
    readonly_fields = ("id", "status", "archived_at", "created_at", "updated_at")
    fields = (
        "id",
        "name",
        "notes",
        "status",
        "created_by",
        "archived_at",
        "created_at",
        "updated_at",
    )
    actions = ("archive_selected", "restore_selected")

    @admin.action(description="Archive selected clients")
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[Client]) -> None:
        for client in queryset:
            client.archive()

    @admin.action(description="Restore selected clients")
    def restore_selected(self, request: HttpRequest, queryset: QuerySet[Client]) -> None:
        for client in queryset:
            client.restore()


class ProfileInline(admin.TabularInline):  # type: ignore[type-arg]
    model = CampaignProfile
    fields = ("version", "created_at", "created_by", "change_note")
    readonly_fields = fields
    extra = 0
    can_delete = False
    ordering = ("-version",)

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Campaign)
class CampaignAdmin(NoDeleteAdmin):
    list_display = ("name", "client", "status", "current_profile", "created_at", "archived_at")
    list_filter = ("status", "client")
    search_fields = ("name", "client__name")
    readonly_fields = (
        "id",
        "client",
        "status",
        "current_profile",
        "created_by",
        "archived_at",
        "created_at",
        "updated_at",
    )
    fields = ("id", "client", "name", *readonly_fields[2:])
    inlines = (ProfileInline,)
    actions = ("archive_selected", "restore_selected")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @admin.action(description="Archive selected campaigns")
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[Campaign]) -> None:
        for campaign in queryset:
            campaign.archive()

    @admin.action(description="Restore selected campaigns")
    def restore_selected(self, request: HttpRequest, queryset: QuerySet[Campaign]) -> None:
        for campaign in queryset:
            campaign.restore()


@admin.register(ClientMembership)
class ClientMembershipAdmin(NoDeleteAdmin):
    """Grant and change roles here (superusers only in practice); revoke = archive."""

    list_display = ("user", "client", "role", "created_at", "archived_at")
    list_filter = ("role", "client")
    search_fields = ("user__email", "client__name")
    readonly_fields = ("id", "archived_at", "created_at", "updated_at")
    autocomplete_fields = ("user", "client")
    actions = ("archive_selected", "restore_selected")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        # The pair is fixed once created: revoke and grant again instead of moving a row.
        base = tuple(self.readonly_fields)
        return (*base, "user", "client") if obj is not None else base

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        if change:
            change_role(obj.client, obj.user, obj.role, actor=request.user)  # type: ignore[arg-type]
        else:
            grant_membership(obj.client, obj.user, obj.role, actor=request.user)  # type: ignore[arg-type]

    @admin.action(description="Revoke (archive) selected memberships")
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[ClientMembership]) -> None:
        for membership in queryset.filter(archived_at__isnull=True):
            revoke_membership(membership.client, membership.user, actor=request.user)  # type: ignore[arg-type]

    @admin.action(description="Restore selected memberships")
    def restore_selected(self, request: HttpRequest, queryset: QuerySet[ClientMembership]) -> None:
        for membership in queryset.filter(archived_at__isnull=False):
            grant_membership(
                membership.client,
                membership.user,
                membership.role,
                actor=request.user,  # type: ignore[arg-type]
            )


@admin.register(CampaignProfile)
class CampaignProfileAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Immutable versions: view only."""

    list_display = ("campaign", "version", "client", "created_by", "created_at")
    list_filter = ("client",)
    search_fields = ("campaign__name", "offer")
    ordering = ("campaign", "-version")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
