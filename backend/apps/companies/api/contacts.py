"""People at a company (Brief section 8), entered by hand for now.

A person is added with a manual data source (who typed it, and when), so every contact has a
source. A people-data provider adapter will fill the same records later. Ranking and the
primary and secondary targets are a person's call here: nothing is guessed.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies import services
from apps.companies.models import Company, Contact, ContactRole, DataSourceType


class ContactSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    title = serializers.CharField(allow_blank=True)
    profile_url = serializers.CharField(allow_blank=True)
    email = serializers.CharField(allow_blank=True)
    email_status = serializers.CharField()
    relevance_reason = serializers.CharField(allow_blank=True)
    rank = serializers.IntegerField(allow_null=True)
    role = serializers.CharField(help_text="primary, secondary or none.")
    source = serializers.CharField(help_text="Who or what entered this person.")  # type: ignore[assignment]


class ContactCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=300)
    title = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")
    profile_url = serializers.URLField(
        required=False, allow_blank=True, max_length=2000, default=""
    )
    email = serializers.EmailField(required=False, allow_blank=True, max_length=320, default="")
    relevance_reason = serializers.CharField(required=False, allow_blank=True, default="")
    rank = serializers.IntegerField(required=False, allow_null=True, min_value=1, default=None)
    role = serializers.ChoiceField(choices=ContactRole.choices, default=ContactRole.NONE)


class ContactPatchSerializer(serializers.Serializer[Any]):
    contact_id = serializers.UUIDField()
    name = serializers.CharField(required=False, max_length=300)
    title = serializers.CharField(required=False, allow_blank=True, max_length=300)
    profile_url = serializers.URLField(required=False, allow_blank=True, max_length=2000)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=320)
    relevance_reason = serializers.CharField(required=False, allow_blank=True)
    rank = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    role = serializers.ChoiceField(choices=ContactRole.choices, required=False)


def contact_body(contact: Contact) -> dict[str, Any]:
    return {
        "id": contact.pk,
        "name": contact.name,
        "title": contact.title,
        "profile_url": contact.profile_url,
        "email": contact.email,
        "email_status": contact.email_status,
        "relevance_reason": contact.relevance_reason,
        "rank": contact.rank,
        "role": contact.role,
        "source": contact.data_source.name,
    }


def company_contacts(company: Company) -> list[Contact]:
    """Active contacts, best first (primary, secondary, then by rank and name)."""
    order = {"primary": 0, "secondary": 1, "none": 2}
    rows = list(Contact.objects.active().filter(company=company).select_related("data_source"))
    rows.sort(key=lambda c: (order.get(c.role, 3), c.rank is None, c.rank or 0, c.name.lower()))
    return rows


def add_contact_to(company: Company, data: dict[str, Any], user: User) -> Contact:
    """Create a manually entered contact (a manual data source records who typed it)."""
    source = services.create_data_source(
        company.campaign.client,
        DataSourceType.MANUAL,
        f"Entered by {user.name or user.email}",
        user=user,
    )
    return services.create_contact(
        company,
        source,
        data["name"],
        title=data["title"],
        profile_url=data["profile_url"],
        email=data["email"],
        relevance_reason=data["relevance_reason"],
        rank=data["rank"],
        role=data["role"],
        user=user,
    )


class ContactRefSerializer(serializers.Serializer[Any]):
    contact_id = serializers.UUIDField()


def find_contact(company: Company, contact_id: Any) -> Contact:
    """An active contact of this company, or a 400 (never another company's)."""
    contact = (
        Contact.objects.active()
        .filter(company=company, pk=contact_id)
        .select_related("data_source")
        .first()
    )
    if contact is None:
        raise ValidationError({"contact_id": ["No such contact on this company."]})
    return contact


def update_contact_of(company: Company, data: dict[str, Any]) -> Contact:
    """Apply a validated patch (fields and/or role) to one of the company's contacts."""
    contact = find_contact(company, data.pop("contact_id"))
    role = data.pop("role", None)
    try:
        if data:
            services.update_contact(contact, **data)
        if role is not None:
            services.set_contact_role(contact, role)
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise ValidationError(exc.message_dict) from exc
        raise ValidationError({"non_field_errors": exc.messages}) from exc
    contact.refresh_from_db()
    return contact
