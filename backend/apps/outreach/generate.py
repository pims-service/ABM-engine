"""Draft outreach messages from the stored research (Brief sections 9 and 12).

``generate_messages`` asks the LLM for a LinkedIn message and/or a cold email (subject, body,
optional follow-up) in one language, and saves each as a draft ``Message``. The drafts are
grounded on purpose:

* the model sees only the campaign offer, the contact, the company's stored facts and the
  signals the angle cites; it is told never to invent a trigger or a personalization point;
* a draft that cites a signal not offered to it, or that contains a number which appears nowhere
  in that evidence, is dropped (reported in ``rejected``), not saved;
* each saved message cites the signals the model says it used, plus the research source.

A person approves a draft before anything leaves the system, and creating one needs a human
``add`` decision (checked before the model is called, so no money is spent otherwise).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from apps.accounts.models import User
from apps.companies.models import Company, CompanyResearch, Contact
from apps.integrations.framework import PromptSpec
from apps.integrations.framework.errors import ProviderError
from apps.integrations.llm import (
    UNTRUSTED_SYSTEM_NOTE,
    pydantic_schema,
    structured_call,
    wrap_untrusted,
)
from apps.research.models import Signal

from .decisions import ADD, latest_human_decision
from .models import Channel, Message, OutreachAngle
from .services import HumanDecisionRequired, create_message

PROMPT_NAME = "outreach_messages"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"
LINKEDIN_MAX = 300
EMAIL_BODY_MAX = 900
SUBJECT_MAX = 90

_SEPARATORS = "".join(chr(code) for code in (0x66B, 0x66C))  # Arabic decimal and thousands
_NUMBER = re.compile(rf"\d[\d,.{_SEPARATORS}]*")
_ARABIC_DIGITS = {0x660 + digit: str(digit) for digit in range(10)}


class GenerationFailed(Exception):
    """Messages could not be written. The message is safe to show."""


class Draft(BaseModel):
    channel: Literal["linkedin", "email"]
    subject: str | None = Field(default=None, max_length=SUBJECT_MAX)
    body: str = Field(max_length=2000)
    follow_up: str | None = Field(default=None, max_length=1200)
    used_signal_ids: list[str] = Field(default_factory=list, max_length=8)


class Drafts(BaseModel):
    drafts: list[Draft] = Field(min_length=1, max_length=4)


@dataclass
class Generated:
    messages: list[Message] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def _digits(text: str) -> set[str]:
    cleaned = text.translate(_ARABIC_DIGITS)
    return {m.group(0).strip(",.") for m in _NUMBER.finditer(cleaned)} - {""}


def invented_numbers(draft_text: str, evidence: str) -> set[str]:
    """Numbers in the draft that appear nowhere in the evidence."""
    return _digits(draft_text) - _digits(evidence)


def _tone(company: Company, language: str) -> str:
    if language != "ar":
        return "Write natural, warm, professional English."
    if "SA" in company.campaign.current_profile.countries:
        return "Write in natural conversational Saudi Arabic, polite and professional."
    return "Write in clear, polite, professional Arabic."


def build_prompt(
    company: Company,
    contact: Contact,
    angle: OutreachAngle,
    research: CompanyResearch | None,
    signals: Sequence[Signal],
    channels: Sequence[str],
    language: str,
) -> tuple[PromptSpec, str]:
    """The prompt and the evidence text it contains (used to check the draft's numbers)."""
    profile = company.campaign.current_profile
    facts = []
    if research is not None:
        for label, value in (
            ("Industry", research.industry),
            ("What it does", research.description),
            ("Products and services", research.products_services),
            ("Target customers", research.target_customers),
        ):
            if value:
                facts.append(f"{label}: {value}")
    signal_lines = [
        f"[{s.pk}] {s.get_type_display()} ({s.event_date}): {s.evidence}" for s in signals
    ]
    evidence = "\n".join(
        [
            profile.offer,
            company.name,
            contact.name,
            contact.title,
            contact.relevance_reason,
            angle.angle,
            angle.rationale,
            *facts,
            # Without the ids: digits inside an id must not make an invented number look real.
            *(f"{x.get_type_display()} ({x.event_date}): {x.evidence}" for x in signals),
        ]
    )
    system = (
        "You write short outreach drafts for a person to review. Use ONLY the material given: "
        "the offer, the contact, the company facts and the dated events. Never invent a "
        "trigger, a number, a client, a result or a personalization point. If no event is "
        "given, do not mention any event: use the angle only. Mention a dated event only if it "
        "is in the list, and list its id in `used_signal_ids`. No hype, no false urgency, no "
        "promises. LinkedIn: at most 300 characters, no subject. Email: a subject of at most "
        "90 characters and a body of at most 900 characters. `follow_up` is an optional short "
        "second message. Address the contact by first name. "
        + _tone(company, language)
        + " "
        + UNTRUSTED_SYSTEM_NOTE
    )
    material = "\n".join(
        [
            f"Company: {company.name}",
            *facts,
            "Dated events (cite by id):" if signal_lines else "No dated events.",
            *signal_lines,
        ]
    )
    user = (
        f"Offer we sell: {profile.offer}\n"
        f"Why contact this account (angle): {angle.angle}\n"
        f"Because: {angle.rationale}\n"
        f"Contact: {contact.name}, {contact.title or 'title unknown'}\n"
        f"Why this contact: {contact.relevance_reason or 'not given'}\n"
        f"Channels to write: {', '.join(channels)}\n"
        f"Language: {language}\n\n" + wrap_untrusted(material, "company_material")
    )
    return PromptSpec(name=PROMPT_NAME, version=PROMPT_VERSION, system=system, user=user), evidence


def _length_problem(draft: Draft) -> str | None:
    if draft.channel == "linkedin" and len(draft.body) > LINKEDIN_MAX:
        return f"LinkedIn message longer than {LINKEDIN_MAX} characters"
    if draft.channel == "email":
        if not (draft.subject or "").strip():
            return "email without a subject"
        if len(draft.body) > EMAIL_BODY_MAX:
            return f"email longer than {EMAIL_BODY_MAX} characters"
    return None


def _save(
    angle: OutreachAngle,
    contact: Contact,
    channel: str,
    language: str,
    body: str,
    subject: str,
    is_followup: bool,
    **common: Any,
) -> Message:
    """Save a draft; a new draft for the same slot replaces (supersedes) the current one."""
    current = Message.objects.latest_for(angle, contact, channel, is_followup)
    return create_message(
        angle,
        contact,
        channel,
        language,
        body,
        subject=subject,
        is_followup=is_followup,
        supersedes=current,
        **common,
    )


def generate_messages(
    company: Company,
    contact: Contact,
    channels: Sequence[str],
    language: str,
    adapter: Any,
    *,
    user: User | None = None,
) -> Generated:
    """Write and save draft messages for ``contact``. Raises ``HumanDecisionRequired`` before any
    model call unless the latest human decision is ``add``, and ``GenerationFailed`` for a
    missing angle or an unusable model answer."""
    if latest_human_decision(company) != ADD:
        raise HumanDecisionRequired("Decide ADD for this account before writing messages.")
    if contact.company_id != company.pk or contact.is_archived:
        raise ValidationError({"contact_id": "Choose a person of this company."})
    wanted = [c for c in dict.fromkeys(channels) if c in (Channel.LINKEDIN, Channel.EMAIL)]
    if not wanted:
        raise ValidationError({"channels": "Choose LinkedIn, email or both."})
    angle = OutreachAngle.objects.filter(company=company).order_by("-created_at", "-id").first()
    if angle is None:
        raise GenerationFailed("There is no outreach angle yet. Run the analysis first.")

    research = CompanyResearch.objects.latest_for(company)
    signals = list(angle.signals.all())
    prompt, evidence = build_prompt(company, contact, angle, research, signals, wanted, language)
    try:
        result = structured_call(adapter, prompt, pydantic_schema(Drafts), max_tokens=2500)
    except ProviderError as error:
        raise GenerationFailed(str(error)) from error
    model = getattr(result.usage, "model", None) or str(
        getattr(adapter, "config", {}).get("model", "")
    )

    by_id = {str(s.pk): s for s in signals}
    sources = [research.data_source] if research is not None else []
    out = Generated()
    parsed = result.data
    if parsed is None:
        raise GenerationFailed("The AI returned no drafts.")
    for draft in parsed.drafts:
        if draft.channel not in wanted:
            continue
        problem = _length_problem(draft)
        text = " ".join(filter(None, [draft.subject, draft.body, draft.follow_up]))
        extra = invented_numbers(text, evidence)
        if problem is None and extra:
            problem = f"mentions numbers that are not in the research ({', '.join(sorted(extra))})"
        if problem is None and any(i not in by_id for i in draft.used_signal_ids):
            problem = "cites an event that was not provided"
        if problem is not None:
            out.rejected.append(f"{draft.channel}: {problem}")
            continue
        used = [by_id[i] for i in draft.used_signal_ids]
        subject = (draft.subject or "").strip() if draft.channel == "email" else ""
        common: dict[str, Any] = {
            "signals": used,
            "data_sources": sources,
            "model_name": model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "user": user,
        }
        out.messages.append(
            _save(angle, contact, draft.channel, language, draft.body, subject, False, **common)
        )
        if draft.follow_up and draft.follow_up.strip():
            follow_subject = f"Re: {subject}" if subject else ""
            out.messages.append(
                _save(
                    angle,
                    contact,
                    draft.channel,
                    language,
                    draft.follow_up,
                    follow_subject,
                    True,
                    **common,
                )
            )
    if not out.messages:
        raise GenerationFailed(
            "The AI's drafts were not usable: " + "; ".join(out.rejected or ["no drafts returned"])
        )
    return out
