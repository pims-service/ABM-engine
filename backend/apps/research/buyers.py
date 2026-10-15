"""Who to contact first at a company (Brief sections 8 and 15): an explainable ranking.

Pure rules over the people already entered for the company, the campaign's preferred titles and
target departments, and the company's size. Nothing is guessed and no model is used:

* The campaign's ordered ``preferred_buyer_titles`` decide first (an exact title beats a title that
  contains the preferred one). Titles are compared after normalising common short forms
  (VP, BD, CEO, CCO ...), so "Vice President Business Development" matches "VP BD".
* Small company (under 50 employees): the founder or CEO usually decides, so founders, the CEO
  and the managing director rank first. This is the brief's small company example.
* Large company: the CEO is not assumed to be the best target. Without preferred titles the
  brief's large company order is used (BD leaders, commercial leaders, then the managing director
  and the CEO).
* Unknown size: the campaign's order is used as it is, and the answer says so.
* A person whose title matches nothing is ranked last and no primary is suggested from them.

Every position comes with the reasons in plain words, so the ranking can be understood and
corrected. The result is a suggestion; applying it is a separate step a person takes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.companies import services
from apps.companies.models import Company, CompanyResearch, Contact, ContactRole

SMALL_COMPANY_MAX = 49
DEFAULT_LARGE_TITLES = (
    "VP BD",
    "Head of BD",
    "Chief Commercial Officer",
    "Commercial Director",
    "Sales Director",
    "Partnerships Director",
    "Managing Director",
    "CEO",
)
# Small company order: founder first, then CEO, managing director and owner.
SMALL_TIERS = ("founder", "ceo", "md", "owner")

_PHRASES = (
    (r"\bvice presidents?\b", "vp"),
    (r"\bchief executive officer\b", "ceo"),
    (r"\bchief commercial officer\b", "cco"),
    (r"\bchief marketing officer\b", "cmo"),
    (r"\bchief revenue officer\b", "cro"),
    (r"\bmanaging director\b", "md"),
    (r"\bbusiness development\b", "bd"),
    (r"\bbiz ?dev\b", "bd"),
    (r"\bco ?-?founders?\b", "founder"),
    (r"\bfounders?\b", "founder"),
    (r"\bdirector of\b", "director"),
    (r"\bhead of\b", "head"),
)
_STOP = {"of", "the", "and", "for", "&", "at", "in"}


def _canon(title: str) -> list[str]:
    text = re.sub(r"[^a-z0-9& ]+", " ", title.lower())
    for pattern, short in _PHRASES:
        text = re.sub(pattern, short, text)
    return [t for t in text.split() if t not in _STOP]


@dataclass
class RankedPerson:
    contact_id: Any
    name: str
    title: str
    rank: int
    suggested_role: str
    reasons: list[str] = field(default_factory=list)
    score: int = 0


@dataclass
class Ranking:
    note: str
    people: list[RankedPerson]


def _match(title: list[str], preferred: list[str]) -> tuple[int, int] | None:
    """(position, quality) of the best preferred title: quality 2 is exact, 1 is contains."""
    best: tuple[int, int] | None = None
    for index, wanted in enumerate(preferred):
        tokens = _canon(wanted)
        if not tokens:
            continue
        if tokens == title:
            found = (index, 2)
        elif set(tokens) <= set(title):
            found = (index, 1)
        else:
            continue
        if best is None or (found[1], -found[0]) > (best[1], -best[0]):
            best = found
    return best


def _small_tier(tokens: list[str]) -> int | None:
    for tier, word in enumerate(SMALL_TIERS):
        if word in tokens or (word == "md" and "managing" in tokens):
            return tier
    return None


def rank_people(
    people: list[tuple[Any, str, str]],
    *,
    preferred: list[str],
    departments: list[str],
    employees: int | None,
) -> Ranking:
    """Rank ``(id, name, title)`` triples. See the module docstring for the rules."""
    small = employees is not None and employees <= SMALL_COMPANY_MAX
    large = employees is not None and not small
    titles = list(preferred) or (list(DEFAULT_LARGE_TITLES) if large else [])
    dept_words = {w for d in departments for w in _canon(d)}
    if employees is None:
        note = "Company size is unknown, so your preferred title order is used as it is."
    elif small:
        note = f"Small company (about {employees} employees): the founder or CEO usually decides."
    else:
        note = f"Larger company (about {employees} employees): the CEO is not assumed to be best."

    scored: list[RankedPerson] = []
    for contact_id, name, title in people:
        tokens = _canon(title)
        reasons: list[str] = []
        score = 0
        tier = _small_tier(tokens) if small else None
        if tier is not None:
            score += 1000 - tier * 50
            reasons.append(
                f"Small company (about {employees} employees): "
                f"{title} is usually the decision maker."
            )
        match = _match(tokens, titles)
        if match is not None:
            index, quality = match
            score += (200 if quality == 2 else 150) - index * 10
            wording = "is" if quality == 2 else "contains"
            reasons.append(f"Title {wording} your preferred title #{index + 1} ({titles[index]}).")
        if large and _small_tier(tokens) is not None:
            reasons.append(
                f"Larger company (about {employees} employees): the CEO or founder is not assumed "
                "to be the best target."
            )
        overlap = sorted(dept_words & set(tokens))
        if overlap:
            score += 3 * len(overlap)
            reasons.append(f"Works in a target department ({', '.join(overlap)}).")
        if not reasons:
            reasons.append("The title matches no preferred title and no target department.")
        scored.append(RankedPerson(contact_id, name, title, 0, ContactRole.NONE, reasons, score))

    scored.sort(key=lambda p: (-p.score, p.name.lower(), str(p.contact_id)))
    for position, person in enumerate(scored, start=1):
        person.rank = position
    useful = [p for p in scored if p.score > 0]
    if useful:
        useful[0].suggested_role = ContactRole.PRIMARY
    if len(useful) > 1:
        useful[1].suggested_role = ContactRole.SECONDARY
    return Ranking(note, scored)


# ------------------------------------------------------------------ the database side


def ranking_for(company: Company) -> Ranking:
    profile = company.campaign.current_profile
    research = CompanyResearch.objects.latest_for(company)
    contacts = Contact.objects.active().filter(company=company).order_by("name", "id")
    return rank_people(
        [(c.pk, c.name, c.title) for c in contacts],
        preferred=list(profile.preferred_buyer_titles),
        departments=list(profile.target_departments),
        employees=research.employee_count if research is not None else None,
    )


def apply_ranking(company: Company) -> Ranking:
    """Save the ranks and the primary and secondary roles of the current suggestion."""
    ranking = ranking_for(company)
    by_id = {c.pk: c for c in Contact.objects.active().filter(company=company)}
    try:
        for person in ranking.people:
            contact = by_id[person.contact_id]
            services.update_contact(contact, rank=person.rank)
        for person in ranking.people:  # free the slots first, then give them out
            contact = by_id[person.contact_id]
            if person.suggested_role == ContactRole.NONE and contact.role != ContactRole.NONE:
                services.set_contact_role(contact, ContactRole.NONE)
        for person in ranking.people:
            if person.suggested_role != ContactRole.NONE:
                services.set_contact_role(by_id[person.contact_id], person.suggested_role)
    except DjangoValidationError as exc:
        raise ValidationError({"non_field_errors": exc.messages}) from exc
    return ranking


class RankedPersonSerializer(serializers.Serializer[Any]):
    contact_id = serializers.UUIDField()
    name = serializers.CharField()
    title = serializers.CharField(allow_blank=True)
    rank = serializers.IntegerField()
    suggested_role = serializers.CharField(help_text="primary, secondary or none.")
    reasons = serializers.ListField(child=serializers.CharField())


class BuyerRankingSerializer(serializers.Serializer[Any]):
    note = serializers.CharField(help_text="How the company's size shaped the ranking.")
    people = RankedPersonSerializer(many=True)


def ranking_body(ranking: Ranking) -> dict[str, Any]:
    return {
        "note": ranking.note,
        "people": [
            {
                "contact_id": p.contact_id,
                "name": p.name,
                "title": p.title,
                "rank": p.rank,
                "suggested_role": p.suggested_role,
                "reasons": p.reasons,
            }
            for p in ranking.people
        ],
    }
