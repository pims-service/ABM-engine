"""Django admin for users (issue #54).

Superusers only: a staff member who could edit users could grant themselves the superuser flag.
The password hash is never displayed (not even Django's truncated summary): the form shows only
whether a usable password is set, and the stock "change password" form stays the one way to set
it. Client roles are not edited here, see ``Client memberships``.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.hashers import is_password_usable
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.admin_base import SuperuserOnlyMixin

from .models import User


@admin.register(User)
class UserAdmin(SuperuserOnlyMixin, DjangoUserAdmin):  # type: ignore[type-arg]
    ordering = ("email",)
    list_display = ("email", "name", "is_active", "is_staff", "is_superuser", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("email", "name")
    date_hierarchy = "created_at"
    readonly_fields = ("id", "password_status", "last_login", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("id", "email", "password_status")}),
        ("Personal info", {"fields": ("name",)}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )

    @admin.display(description="Password")
    def password_status(self, obj: User | None) -> SafeString | str:
        if obj is None or not obj.pk:
            return "-"
        state = "Set" if is_password_usable(obj.password) else "Not set (cannot log in)"
        url = reverse("admin:auth_user_password_change", args=[obj.pk])
        return format_html('{} (hash hidden). <a href="{}">Change password</a>', state, url)


class GroupAdmin(SuperuserOnlyMixin, DjangoGroupAdmin):
    """Django permission groups: they decide which models a non-superuser staff member sees."""


admin.site.unregister(Group)
admin.site.register(Group, GroupAdmin)
