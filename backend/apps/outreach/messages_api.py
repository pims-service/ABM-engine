"""Serializers and helpers for the message endpoints of a company (Brief sections 11 and 12)."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.companies.models import Company, Contact
from apps.integrations.llm_connection import LLMUnavailable, get_llm_adapter

from .generate import GenerationFailed, generate_messages
from .models import Channel, Message
from .services import approve_message, mark_exported, supersede_message


class EvidenceSerializer(serializers.Serializer[Any]):
    type_label = serializers.CharField()
    event_date = serializers.DateField()


class MessageSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    contact_id = serializers.UUIDField()
    contact_name = serializers.CharField()
    contact_title = serializers.CharField(allow_blank=True)
    channel = serializers.CharField()
    language = serializers.CharField()
    subject = serializers.CharField(allow_blank=True)
    body = serializers.CharField()
    is_followup = serializers.BooleanField()
    status = serializers.CharField(help_text="draft, approved or exported.")
    approved_by = serializers.CharField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    exported_at = serializers.DateTimeField(allow_null=True)
    evidence = EvidenceSerializer(many=True)
    created_at = serializers.DateTimeField()


class GenerateRequestSerializer(serializers.Serializer[Any]):
    contact_id = serializers.UUIDField(
        required=False, help_text="Default: the company's primary contact."
    )
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=[Channel.LINKEDIN, Channel.EMAIL]),
        min_length=1,
        max_length=2,
    )
    language = serializers.CharField(max_length=16)


class GenerateResultSerializer(serializers.Serializer[Any]):
    messages = MessageSerializer(many=True)
    rejected = serializers.ListField(
        child=serializers.CharField(), help_text="Drafts the safety checks refused, with why."
    )


class MessageRefSerializer(serializers.Serializer[Any]):
    message_id = serializers.UUIDField()


class MessageEditSerializer(MessageRefSerializer):
    body = serializers.CharField(max_length=5000)
    subject = serializers.CharField(required=False, allow_blank=True, max_length=300)


def message_body(message: Message) -> dict[str, Any]:
    approver = message.approved_by
    return {
        "id": message.pk,
        "contact_id": message.contact_id,
        "contact_name": message.contact.name,
        "contact_title": message.contact.title,
        "channel": message.channel,
        "language": message.language,
        "subject": message.subject,
        "body": message.body,
        "is_followup": message.is_followup,
        "status": message.status,
        "approved_by": (approver.name or approver.email) if approver else None,
        "approved_at": message.approved_at,
        "exported_at": message.exported_at,
        "evidence": [
            {"type_label": s.get_type_display(), "event_date": s.event_date}
            for s in message.signals.all()
        ],
        "created_at": message.created_at,
    }


def company_messages(company: Company) -> list[Message]:
    """Current messages (nothing supersedes them): per person, channel, then message before
    follow-up."""
    rows = list(
        Message.objects.for_company(company)
        .current()
        .select_related("contact", "approved_by")
        .prefetch_related("signals")
    )
    rows.sort(key=lambda m: (m.contact.name.lower(), m.channel, m.is_followup, m.created_at))
    return rows


def _django_errors(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "error_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"non_field_errors": exc.messages})


def find_message(company: Company, message_id: Any) -> Message:
    message = (
        Message.objects.for_company(company)
        .select_related("contact", "angle", "approved_by")
        .filter(pk=message_id)
        .first()
    )
    if message is None:
        raise ValidationError({"message_id": ["No such message on this company."]})
    return message


def generate_for(company: Company, data: dict[str, Any], user: Any) -> dict[str, Any]:
    """Pick the contact, get the client's LLM, write the drafts."""
    contact: Contact | None
    if data.get("contact_id"):
        contact = Contact.objects.active().filter(company=company, pk=data["contact_id"]).first()
    else:
        contact = Contact.objects.primary_for(company)
    if contact is None:
        raise ValidationError(
            {"contact_id": ["Add a person and make them the primary target first."]}
        )
    try:
        adapter = get_llm_adapter(company.client_id)
        generated = generate_messages(
            company,
            contact,
            data["channels"],
            data["language"].strip().lower(),
            adapter,
            user=user,
        )
    except LLMUnavailable as exc:
        raise ValidationError({"non_field_errors": [str(exc)]}) from exc
    except GenerationFailed as exc:
        raise ValidationError({"non_field_errors": [str(exc)]}) from exc
    except DjangoValidationError as exc:
        raise _django_errors(exc) from exc
    rows = [message_body(find_message(company, m.pk)) for m in generated.messages]
    return {"messages": rows, "rejected": generated.rejected}


def approve_for(company: Company, message_id: Any, user: Any) -> Message:
    message = find_message(company, message_id)
    try:
        approve_message(message, user)
    except DjangoValidationError as exc:
        raise _django_errors(exc) from exc
    return find_message(company, message_id)


def export_for(company: Company, message_id: Any, user: Any) -> Message:
    message = find_message(company, message_id)
    try:
        mark_exported(message, user)
    except DjangoValidationError as exc:
        raise _django_errors(exc) from exc
    return find_message(company, message_id)


def edit_for(company: Company, data: dict[str, Any], user: Any) -> Message:
    """Editing writes a new draft that supersedes the old message (which is kept)."""
    old = find_message(company, data["message_id"])
    try:
        new = supersede_message(old, data["body"], subject=data.get("subject"), user=user)
    except DjangoValidationError as exc:
        raise _django_errors(exc) from exc
    return find_message(company, new.pk)
