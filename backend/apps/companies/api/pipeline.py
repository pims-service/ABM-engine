"""Outreach progress and the export of approved accounts (Brief sections 13 and 22).

* ``outreach_stage`` / ``stages_for``: how far an account got after a person said ADD, from the
  ``contacted``, ``replied`` and ``meeting_booked`` entries of its timeline.
* ``record_outreach``: log one such step (only for an account with a human ADD decision).
* ``approved_accounts_csv``: one row per ADD account with its research, its primary person, its
  angle and its approved messages, ready for the tool that sends them. Cells are protected
  against spreadsheet formula injection.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ValidationError

from apps.companies.models import Company, CompanyResearch, Contact
from apps.outreach.decisions import ADD, latest_human_decision
from apps.outreach.models import Activity, ActivityType, Message, MessageStatus, OutreachAngle
from apps.outreach.services import record_activity
from apps.research.models import AIRecommendation, ICPAssessment, Signal

STAGES = ("none", "contacted", "replied", "meeting_booked")
_RANK = {
    ActivityType.CONTACTED: 1,
    ActivityType.REPLIED: 2,
    ActivityType.MEETING_BOOKED: 3,
}
STAGE_ACTIVITY = {
    "contacted": ActivityType.CONTACTED,
    "replied": ActivityType.REPLIED,
    "meeting_booked": ActivityType.MEETING_BOOKED,
}


def stages_for(company_ids: Iterable[Any]) -> dict[Any, str]:
    """The furthest outreach stage reached, per company (companies with none are absent)."""
    best: dict[Any, int] = {}
    rows = Activity.objects.filter(company__in=list(company_ids), type__in=list(_RANK))
    for company_id, kind in rows.values_list("company_id", "type"):
        best[company_id] = max(best.get(company_id, 0), _RANK[ActivityType(kind)])
    return {cid: STAGES[rank] for cid, rank in best.items()}


def outreach_stage(company: Company) -> str:
    return stages_for([company.pk]).get(company.pk, "none")


def record_outreach(company: Company, stage: str, note: str, user: Any) -> str:
    """Log that the account was contacted, replied or booked a meeting."""
    if stage not in STAGE_ACTIVITY:
        raise ValidationError({"stage": "Choose contacted, replied or meeting_booked."})
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")
    if latest_human_decision(company) != ADD:
        raise ValidationError("Only an account you decided ADD for can move through outreach.")
    if note.strip():
        record_activity(company, STAGE_ACTIVITY[stage], user, note=note.strip())
    else:
        record_activity(company, STAGE_ACTIVITY[stage], user)
    return outreach_stage(company)


# ------------------------------------------------------------------ the export

COLUMNS = (
    "Company",
    "Website",
    "Country",
    "Industry",
    "ICP fit",
    "Trigger",
    "Trigger types",
    "AI recommendation",
    "Outreach stage",
    "Contact name",
    "Contact title",
    "Contact email",
    "Contact profile",
    "Outreach angle",
    "LinkedIn message",
    "Email subject",
    "Email body",
    "Message status",
)


def safe(value: Any) -> str:
    """Text for a spreadsheet cell: a leading =, +, - or @ would be run as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def approved_accounts_csv(companies: Iterable[Company]) -> str:
    """CSV of the given companies (the caller passes the ones with a human ADD decision)."""
    rows = list(companies)
    ids = [c.pk for c in rows]
    research = {r.company_id: r for r in CompanyResearch.objects.current().filter(company__in=ids)}
    fits = {a.company_id: a for a in ICPAssessment.objects.current().filter(company__in=ids)}
    recs = {r.company_id: r for r in AIRecommendation.objects.current().filter(company__in=ids)}
    types: dict[Any, list[str]] = defaultdict(list)
    for signal in Signal.objects.fresh().filter(company__in=ids).order_by("-event_date"):
        label = signal.get_type_display()
        if label not in types[signal.company_id]:
            types[signal.company_id].append(label)
    contacts = {
        c.company_id: c for c in Contact.objects.active().filter(company__in=ids, role="primary")
    }
    angles: dict[Any, OutreachAngle] = {}
    for angle in OutreachAngle.objects.filter(company__in=ids).order_by("created_at"):
        angles[angle.company_id] = angle  # the newest wins
    messages: dict[Any, list[Message]] = defaultdict(list)
    sendable = [MessageStatus.APPROVED, MessageStatus.EXPORTED]
    for message in Message.objects.current().filter(company__in=ids, status__in=sendable):
        messages[message.company_id].append(message)
    stages = stages_for(ids)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for company in rows:
        snap = research.get(company.pk)
        contact = contacts.get(company.pk)
        mine = messages.get(company.pk, [])
        linkedin = next((m for m in mine if m.channel == "linkedin" and not m.is_followup), None)
        email = next((m for m in mine if m.channel == "email" and not m.is_followup), None)
        latest_angle = angles.get(company.pk)
        statuses = sorted({m.status for m in mine})
        writer.writerow(
            [
                safe(v)
                for v in (
                    company.name,
                    company.website,
                    company.country,
                    snap.industry if snap else "",
                    fits[company.pk].fit if company.pk in fits else "",
                    "yes" if types.get(company.pk) else "no",
                    "; ".join(types.get(company.pk, [])),
                    recs[company.pk].status if company.pk in recs else "",
                    stages.get(company.pk, "none"),
                    contact.name if contact else "",
                    contact.title if contact else "",
                    contact.email if contact else "",
                    contact.profile_url if contact else "",
                    latest_angle.angle if latest_angle else "",
                    linkedin.body if linkedin else "",
                    email.subject if email else "",
                    email.body if email else "",
                    ", ".join(statuses),
                )
            ]
        )
    return out.getvalue()
