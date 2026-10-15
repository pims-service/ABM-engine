"""Push an approved account to the CRM (Brief section 12: "approve message, push to CRM").

Preconditions, each with a plain message: a human ADD decision, a primary person with an email
address, and at least one approved message. The CRM record gets the person, the company name, a tag
and one note with the research summary, the angle and the approved messages (stored facts only).
The approved messages are then marked exported (which logs it on the timeline). Pushing again
updates the same CRM contact (the CRM matches on the email) and never adds the note twice.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies.models import Company, Contact
from apps.integrations.framework import CRMContact
from apps.research.models import AIRecommendation, ICPAssessment
from apps.research.services import company_trigger_state

from .decisions import ADD, latest_human_decision
from .models import Message, MessageStatus, OutreachAngle
from .services import mark_exported

PART_CHARS = 480


class PushBlocked(ValidationError):
    """The account is not ready to push. The message says what to do."""


@dataclass(frozen=True)
class Pushed:
    action: str
    remote_id: str
    exported: int


def _ready(company: Company) -> tuple[Contact, list[Message]]:
    if latest_human_decision(company) != ADD:
        raise PushBlocked("Only an account you decided ADD for can be pushed to the CRM.")
    contact = Contact.objects.primary_for(company)
    if contact is None:
        raise PushBlocked("Make someone the primary target first.")
    if not contact.email:
        raise PushBlocked(
            f"{contact.name} has no email address. Add one (the CRM needs it to match the person)."
        )
    sendable = [MessageStatus.APPROVED, MessageStatus.EXPORTED]
    messages = list(
        Message.objects.current()
        .filter(company=company, contact=contact, status__in=sendable)
        .order_by("channel", "is_followup", "created_at")
    )
    if not messages:
        raise PushBlocked("Approve at least one message first.")
    return contact, messages


def _summary(company: Company) -> list[str]:
    assessment = ICPAssessment.objects.latest_for(company)
    recommendation = AIRecommendation.objects.latest_for(company)
    state = company_trigger_state(company)
    types = sorted({s.get_type_display() for s in state.signals})
    lines = [f"ABM Engine: {company.name}" + (f" ({company.domain})" if company.domain else "")]
    facts = []
    if assessment is not None:
        facts.append(f"ICP fit: {assessment.fit}")
    if recommendation is not None:
        facts.append(f"AI recommended: {recommendation.status}")
    facts.append("Trigger: " + (", ".join(types) if types else "none"))
    lines.append(" | ".join(facts))
    angle = OutreachAngle.objects.filter(company=company).order_by("-created_at").first()
    if angle is not None:
        lines.append(f"Angle: {angle.angle}")
    return lines


def _lines(messages: list[Message]) -> list[str]:
    out: list[str] = []
    for message in messages:
        label = {"linkedin": "LinkedIn", "email": "Email"}.get(message.channel, message.channel)
        if message.is_followup:
            label += " follow-up"
        if message.subject:
            out.append(f"{label} subject: {message.subject}")
        out.append(f"{label}: {message.body}")
    return out


def note_fields(company: Company, messages: list[Message]) -> dict[str, str]:
    """The note as ordered short parts (``note_1`` ...): CRM payload fields are length limited."""
    parts: list[str] = []
    for line in [*_summary(company), *_lines(messages)]:
        parts.extend(textwrap.wrap(line, PART_CHARS) or [""])
    return {f"note_{i}": part for i, part in enumerate(parts, start=1) if part}


def push_account(
    company: Company, adapter: Any, user: User | None, *, refresh_note: bool = False
) -> Pushed:
    """Push ``company``'s primary person (see the module docstring). Raises ``PushBlocked``."""
    if company.is_archived:
        raise PushBlocked("Archived companies are read-only.")
    contact, messages = _ready(company)
    fields = {"company_name": company.name, **note_fields(company, messages)}
    if refresh_note:
        fields["refresh_note"] = "1"
    result = adapter.push_contact(
        CRMContact(
            external_key=f"contact:{contact.pk}",
            name=contact.name,
            email=contact.email,
            title=contact.title or None,
            company_domain=company.domain,
            fields=fields,
        )
    )
    exported = 0
    for message in messages:
        if message.status == MessageStatus.APPROVED:
            mark_exported(message, user)
            exported += 1
    return Pushed(result.data.action, result.data.remote_id, exported)
