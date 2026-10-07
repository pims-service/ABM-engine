"""Django admin for companies (issue #54). View only; archive and restore are actions.

Research snapshots and data sources are append-only. Companies are created through
``services.create_company`` (the admin cannot dedupe) and contacts through ``create_contact`` /
``update_contact`` (they keep the one primary and one secondary rule), so none of them has an
add, change or delete button.

Contacts hold personal data. Superusers see a contact's email and profile URL; other staff see a
masked email (``j***@example.com``), no profile URL, and cannot search by email.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.core.admin_base import ReadOnlyAdmin, is_superuser, mask_email

from .models import Company, CompanyResearch, Contact, DataSource
from .services import restore_contact

#: Contact columns that identify a person beyond their name and title.
CONTACT_PII_FIELDS = ("email", "profile_url")


@admin.register(Company)
class CompanyAdmin(ReadOnlyAdmin):
    list_display = ("name", "domain", "campaign", "client", "status", "input_source", "archived_at")
    list_filter = ("status", "input_source", "client")
    list_select_related = ("campaign", "client")
    search_fields = ("name", "domain", "website")
    date_hierarchy = "created_at"
    actions = ("archive_selected", "restore_selected")

    @admin.action(description="Archive selected companies")
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[Company]) -> None:
        for company in queryset:
            company.archive()

    @admin.action(description="Restore selected companies")
    def restore_selected(self, request: HttpRequest, queryset: QuerySet[Company]) -> None:
        for company in queryset:
            company.restore()


@admin.register(CompanyResearch)
class CompanyResearchAdmin(ReadOnlyAdmin):
    list_display = ("company", "researched_at", "data_source", "industry", "employee_count")
    list_filter = ("client",)
    list_select_related = ("company", "data_source")
    search_fields = ("company__name", "company__domain")
    date_hierarchy = "researched_at"
    ordering = ("-researched_at", "-created_at")


@admin.register(Contact)
class ContactAdmin(ReadOnlyAdmin):
    """Contacts carry personal data: view only, with archive and restore actions."""

    list_display = (
        "name",
        "title",
        "company",
        "role",
        "email_status",
        "masked_email",
        "rank",
        "archived_at",
    )
    list_filter = ("role", "email_status", "client")
    list_select_related = ("company",)
    date_hierarchy = "created_at"
    actions = ("archive_selected", "restore_selected")

    def get_search_fields(self, request: HttpRequest) -> tuple[str, ...]:
        # Searching by email would reveal it one guess at a time, so only superusers may.
        fields = ("name", "title", "company__name")
        return (*fields, "email") if is_superuser(request) else fields

    def get_fields(self, request: HttpRequest, obj: Any = None) -> Any:
        names = [f.name for f in Contact._meta.fields if f.name not in CONTACT_PII_FIELDS]
        # The admin object is shared between requests, so the choice is made here per request
        # (not stored on self): real fields for a superuser, masked stand-ins for everyone else.
        pii = list(CONTACT_PII_FIELDS) if is_superuser(request) else ["masked_email", "profile"]
        return [*names, *pii]

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return tuple(self.get_fields(request, obj))

    @admin.display(description="Email (masked)")
    def masked_email(self, obj: Contact) -> str:
        return mask_email(obj.email)

    @admin.display(description="Profile URL")
    def profile(self, obj: Contact) -> str:
        return "(hidden)" if obj.profile_url else "-"

    @admin.action(description="Archive selected contacts")
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[Contact]) -> None:
        for contact in queryset:
            contact.archive()

    @admin.action(description="Restore selected contacts")
    def restore_selected(self, request: HttpRequest, queryset: QuerySet[Contact]) -> None:
        for contact in queryset:
            restore_contact(contact)


@admin.register(DataSource)
class DataSourceAdmin(ReadOnlyAdmin):
    list_display = ("name", "type", "client", "retrieved_at", "evidence_date")
    list_filter = ("type", "client")
    list_select_related = ("client",)
    search_fields = ("name", "url")
    date_hierarchy = "retrieved_at"
