"""Write path for angles, messages and activities (issue #43).

Permission checks belong to the API layer (issue #46); these functions enforce data rules.

* ``create_angle``: append an angle citing signals and data sources (same company / client).
* ``create_message``: a draft grounded in cited evidence. Needs the company's latest human
  decision to be ``add`` (ADR 0007).
* ``approve_message``: draft -> approved (same decision rule). ``mark_exported``: approved ->
  exported, and logs an ``exported`` activity.
* ``supersede_message``: the only way to "edit" a message: a new draft pointing at the old one.
* ``record_activity`` (alias ``log_activity``) and ``record_feedback``: timeline entries, with the
  Brief section 16 feedback payload validated.

Grounding (Brief sections 12, 22): evidence is stored as references to rows that already exist
for the company. A signal must belong to the same company, a data source to the same client,
and a message may only cite sources reachable through its company (the angle, any cited
signal, the company's signals, research snapshots and contacts). Nothing is free text.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import TextChoices
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company, CompanyResearch, Contact, DataSource
from apps.research.models import Signal

from .decisions import ADD, DecisionLookup, latest_human_decision
from .models import (
    Activity,
    ActivityType,
    AngleSignal,
    AngleSource,
    Channel,
    Message,
    MessageSignal,
    MessageSource,
    MessageStatus,
    OutreachAngle,
)

__all__ = [
    "FeedbackKind",
    "FeedbackTargetType",
    "HumanDecisionRequired",
    "approve_message",
    "create_angle",
    "create_message",
    "log_activity",
    "mark_exported",
    "record_activity",
    "record_feedback",
    "supersede_message",
]

FEEDBACK_NOTE_MAX = 2000


class HumanDecisionRequired(ValidationError):
    """The company's latest human decision is not ``add``, so outreach is not allowed."""


class FeedbackKind(TextChoices):
    """Brief section 16 feedback flags."""

    WRONG_BUYER = "wrong_buyer", "Wrong buyer"
    NOT_B2B = "not_b2b", "Company is not B2B"
    UNSUITABLE_INDUSTRY = "unsuitable_industry", "Industry is unsuitable"
    NOT_A_TRIGGER = "not_a_trigger", "Signal should not count as a trigger"
    CEO_SHOULD_BE_PRIMARY = "ceo_should_be_primary", "CEO should be primary"
    CEO_SHOULD_NOT_BE_PRIMARY = "ceo_should_not_be_primary", "CEO should not be primary"
    GOVERNMENT_COMPANY = "government_company", "Company is government"
    INCORRECT_COMPANY_DATA = "incorrect_company_data", "Incorrect company data"
    OTHER = "other", "Other"


class FeedbackTargetType(TextChoices):
    COMPANY = "company", "Company"
    SIGNAL = "signal", "Signal"
    CONTACT = "contact", "Contact"
    ANGLE = "angle", "Outreach angle"
    MESSAGE = "message", "Message"


# ------------------------------------------------------------------ helpers


def _unique(items: Iterable[Any]) -> list[Any]:
    seen: dict[Any, Any] = {}
    for item in items:
        seen.setdefault(item.pk, item)
    return list(seen.values())


def _require_writable(company: Company) -> None:
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")


def _check_signals(company: Company, signals: list[Signal]) -> None:
    for signal in signals:
        if signal.company_id != company.pk:
            raise ValidationError({"signals": "A cited signal belongs to a different company."})


def _check_sources_client(company: Company, sources: list[DataSource]) -> None:
    for source in sources:
        if source.client_id != company.client_id:
            raise ValidationError(
                {"data_sources": "A cited data source belongs to a different client."}
            )


def _company_source_ids(company: Company) -> set[uuid.UUID]:
    """Data sources reachable through the company: research, signals and contacts."""
    ids: set[uuid.UUID] = set()
    for model in (CompanyResearch, Signal, Contact):
        ids.update(model.objects.filter(company=company).values_list("data_source_id", flat=True))
    return ids


def _check_decision(company: Company, lookup: DecisionLookup | None) -> None:
    decision = (lookup or latest_human_decision)(company)
    if decision != ADD:
        shown = decision if decision is not None else "none"
        raise HumanDecisionRequired(
            f"Outreach needs a human decision of '{ADD}' (latest decision: {shown})."
        )


# ------------------------------------------------------------------ angles


@transaction.atomic
def create_angle(
    company: Company,
    angle: str,
    rationale: str,
    *,
    signals: Iterable[Signal] = (),
    data_sources: Iterable[DataSource] = (),
    model_name: str = "",
    prompt_version: str = "",
    schema_version: str = "",
    user: User | None = None,
) -> OutreachAngle:
    """Append an angle. Cited signals must be this company's, data sources this client's.

    An angle may cite nothing (grounded only in campaign rules and research).
    """
    _require_writable(company)
    signal_list, source_list = _unique(signals), _unique(data_sources)
    _check_signals(company, signal_list)
    _check_sources_client(company, source_list)

    row = OutreachAngle(
        company=company,
        angle=angle.strip(),
        rationale=rationale.strip(),
        model_name=model_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        created_by=user,
    )
    row.full_clean(exclude=["client", "company"], validate_unique=False, validate_constraints=False)
    row.save()
    for signal in signal_list:
        AngleSignal(angle=row, signal=signal).save()
    for source in source_list:
        AngleSource(angle=row, data_source=source).save()
    return row


# ------------------------------------------------------------------ messages


def _normalize_language(company: Company, language: str) -> str:
    code = language.strip().lower()
    allowed = list(company.campaign.current_profile.outreach_languages)
    if code not in allowed:
        raise ValidationError(
            {"language": f"'{language}' is not an outreach language of the campaign ({allowed})."}
        )
    return code


def _attach_message_evidence(
    message: Message, signals: list[Signal], sources: list[DataSource]
) -> None:
    for signal in signals:
        MessageSignal(message=message, signal=signal).save()
    for source in sources:
        MessageSource(message=message, data_source=source).save()


@transaction.atomic
def create_message(
    angle: OutreachAngle,
    contact: Contact,
    channel: str,
    language: str,
    body: str,
    *,
    subject: str = "",
    is_followup: bool = False,
    signals: Iterable[Signal] = (),
    data_sources: Iterable[DataSource] = (),
    supersedes: Message | None = None,
    model_name: str = "",
    prompt_version: str = "",
    schema_version: str = "",
    user: User | None = None,
    decision_lookup: DecisionLookup | None = None,
) -> Message:
    """Append a draft message for ``angle`` and ``contact`` (the company is the angle's).

    Raises ``HumanDecisionRequired`` unless the latest human decision is ``add``, and
    ``ValidationError`` for an archived company or contact, a language outside the campaign's,
    a contact or evidence from another company or client, or evidence not reachable through the
    company. Prefer ``supersede_message`` to edit.
    """
    company = angle.company
    _require_writable(company)
    _check_decision(company, decision_lookup)
    if contact.company_id != company.pk:
        raise ValidationError({"contact": "The contact belongs to a different company."})
    if contact.is_archived:
        raise ValidationError({"contact": "The contact is archived."})
    if channel not in Channel.values:
        raise ValidationError({"channel": f"Unknown channel '{channel}'."})
    code = _normalize_language(company, language)

    signal_list, source_list = _unique(signals), _unique(data_sources)
    _check_signals(company, signal_list)
    _check_sources_client(company, source_list)
    reachable = _company_source_ids(company)
    reachable.update(angle.source_links.values_list("data_source_id", flat=True))
    reachable.update(s.data_source_id for s in angle.signals.all())
    reachable.update(s.data_source_id for s in signal_list)
    if any(source.pk not in reachable for source in source_list):
        raise ValidationError(
            {"data_sources": "A cited data source is not part of this company's research."}
        )

    if supersedes is not None:
        if supersedes.company_id != company.pk:
            raise ValidationError({"supersedes": "The message belongs to a different company."})
        if not supersedes.is_current:
            raise ValidationError({"supersedes": "That message is already superseded."})

    message = Message(
        company=company,
        contact=contact,
        angle=angle,
        channel=channel,
        language=code,
        subject=subject.strip(),
        body=body.strip(),
        is_followup=is_followup,
        supersedes=supersedes,
        model_name=model_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        created_by=user,
    )
    message.full_clean(
        exclude=["client", "company", "contact", "angle", "supersedes"],
        validate_unique=False,
        validate_constraints=False,
    )
    if channel == Channel.EMAIL and not message.subject:
        raise ValidationError({"subject": "An email needs a subject."})
    if channel != Channel.EMAIL and message.subject:
        raise ValidationError({"subject": "Only emails have a subject."})
    try:
        with transaction.atomic():
            message.save()
    except IntegrityError as exc:  # lost a race to another edit of the same message
        if supersedes is not None and not supersedes.is_current:
            raise ValidationError({"supersedes": "That message is already superseded."}) from exc
        raise
    _attach_message_evidence(message, signal_list, source_list)
    return message


def supersede_message(
    old: Message,
    body: str,
    *,
    subject: str | None = None,
    language: str | None = None,
    signals: Iterable[Signal] | None = None,
    data_sources: Iterable[DataSource] | None = None,
    model_name: str = "",
    prompt_version: str = "",
    schema_version: str = "",
    user: User | None = None,
    decision_lookup: DecisionLookup | None = None,
) -> Message:
    """Edit a message: a new draft with the same angle, contact, channel and follow-up flag.

    ``old`` is untouched (its text and status stay as they were). Evidence and subject default
    to the old message's. The same decision rule as ``create_message`` applies.
    """
    return create_message(
        old.angle,
        old.contact,
        old.channel,
        language if language is not None else old.language,
        body,
        subject=old.subject if subject is None else subject,
        is_followup=old.is_followup,
        signals=old.signals.all() if signals is None else signals,
        data_sources=old.data_sources.all() if data_sources is None else data_sources,
        supersedes=old,
        model_name=model_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        user=user,
        decision_lookup=decision_lookup,
    )


@transaction.atomic
def approve_message(
    message: Message, user: User, *, decision_lookup: DecisionLookup | None = None
) -> Message:
    """draft -> approved by ``user``. Needs a human ``add`` decision and a current message."""
    locked = Message.objects.select_for_update().get(pk=message.pk)
    _require_writable(locked.company)
    if locked.status != MessageStatus.DRAFT:
        raise ValidationError(f"Only a draft can be approved (this one is {locked.status}).")
    if not locked.is_current:
        raise ValidationError("A superseded message cannot be approved.")
    if locked.contact.is_archived:
        raise ValidationError({"contact": "The contact is archived."})
    _check_decision(locked.company, decision_lookup)
    locked.status = MessageStatus.APPROVED
    locked.approved_by = user
    locked.approved_at = timezone.now()
    locked.save()
    message.refresh_from_db()
    return message


@transaction.atomic
def mark_exported(message: Message, user: User | None = None) -> Message:
    """approved -> exported, and log an ``exported`` activity on the company timeline."""
    locked = Message.objects.select_for_update().get(pk=message.pk)
    if locked.status != MessageStatus.APPROVED:
        raise ValidationError(
            f"Only an approved message can be exported (this one is {locked.status})."
        )
    locked.status = MessageStatus.EXPORTED
    locked.exported_at = timezone.now()
    locked.save()
    record_activity(
        locked.company,
        ActivityType.EXPORTED,
        user,
        message_id=str(locked.pk),
        channel=locked.channel,
    )
    message.refresh_from_db()
    return message


# ------------------------------------------------------------------ activities


def _validate_target(company: Company, target: Any) -> dict[str, str]:
    if not isinstance(target, dict) or set(target) != {"type", "id"}:
        raise ValidationError({"target": 'Target must be {"type": ..., "id": ...}.'})
    kind, raw_id = target["type"], target["id"]
    if kind not in FeedbackTargetType.values:
        raise ValidationError({"target": f"Unknown target type '{kind}'."})
    try:
        pk = uuid.UUID(str(raw_id))
    except ValueError:
        raise ValidationError({"target": "Target id is not a valid id."}) from None
    lookups: dict[str, Any] = {
        FeedbackTargetType.SIGNAL: Signal.objects,
        FeedbackTargetType.CONTACT: Contact.objects,
        FeedbackTargetType.ANGLE: OutreachAngle.objects,
        FeedbackTargetType.MESSAGE: Message.objects,
    }
    if kind == FeedbackTargetType.COMPANY:
        found = pk == company.pk
    else:
        found = lookups[kind].filter(pk=pk, company=company).exists()
    if not found:
        raise ValidationError({"target": f"The {kind} does not belong to this company."})
    return {"type": kind, "id": str(pk)}


def _validate_feedback(company: Company, payload: dict[str, Any]) -> dict[str, Any]:
    """Brief section 16 payload: ``kind`` (required), ``target`` (optional), ``note`` (optional,
    required for ``other``). Anything else is rejected."""
    extra = set(payload) - {"kind", "target", "note"}
    if extra:
        raise ValidationError({"payload": f"Unknown feedback fields: {sorted(extra)}."})
    kind = payload.get("kind")
    if kind not in FeedbackKind.values:
        raise ValidationError({"kind": f"Feedback kind must be one of {FeedbackKind.values}."})
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > FEEDBACK_NOTE_MAX:
        raise ValidationError({"note": f"Note must be text of at most {FEEDBACK_NOTE_MAX} chars."})
    note = note.strip()
    if kind == FeedbackKind.OTHER and not note:
        raise ValidationError({"note": "Feedback of kind 'other' needs a note."})
    clean: dict[str, Any] = {"kind": kind}
    if payload.get("target") is not None:
        clean["target"] = _validate_target(company, payload["target"])
    if note:
        clean["note"] = note
    return clean


def record_activity(
    company: Company,
    type: str,
    actor: User | None = None,
    *,
    created_at: datetime | None = None,
    **payload: Any,
) -> Activity:
    """Insert one timeline row. ``actor`` is ``None`` for the system or an AI step.

    The payload must be JSON. ``feedback`` payloads are validated against Brief section 16
    (see ``record_feedback``). Campaign and client are taken from the company.
    """
    if type not in ActivityType.values:
        raise ValidationError({"type": f"Unknown activity type '{type}'."})
    if type == ActivityType.FEEDBACK:
        payload = _validate_feedback(company, payload)
    else:
        try:
            json.dumps(payload)
        except (TypeError, ValueError):
            raise ValidationError({"payload": "Payload must be JSON-serializable."}) from None
    extra = {} if created_at is None else {"created_at": created_at}
    activity = Activity(company=company, type=type, actor=actor, payload=payload, **extra)
    activity.save()
    return activity


#: The name used in the data model doc and issue text.
log_activity = record_activity


def record_feedback(
    company: Company,
    kind: str,
    *,
    target: dict[str, str] | None = None,
    note: str = "",
    actor: User | None = None,
) -> Activity:
    """Log Brief section 16 feedback as an Activity of type ``feedback``.

    Payload shape: ``{"kind": <FeedbackKind>, "target": {"type": ..., "id": ...}?, "note": str?}``.
    """
    payload: dict[str, Any] = {"kind": kind}
    if target is not None:
        payload["target"] = target
    if note:
        payload["note"] = note
    return record_activity(company, ActivityType.FEEDBACK, actor, **payload)
