"""The AI explanation and tie-break of a buyer ranking (issue #132, Brief sections 8 and 15).

The ranking itself is rules first (``apps.research.buyers``): easy to read and to correct. The
model has two narrow jobs, and only when a run asks for it:

* **explain** why the first person is the best target, in plain words, from the rule reasons;
* **break a tie**: when two or more people score the same at the top, choose between exactly
  those people. The model can never add a person: its answer is checked against the candidates
  it was given, and anything else is refused.

A model answer that is not valid (a person that is not a candidate, a choice outside the tied
people) gets one more try; after that, and when there is no AI connection or the provider fails,
the run keeps the rules ranking with a plain explanation built from the rule reasons. The
explanation says which it is (``"source": "model"`` or ``"rules"``). Token use and cost are
logged with every call by the metered LLM adapter (one ``ProviderCall`` row each).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.companies.models import Company, ContactRole
from apps.integrations.framework import PromptSpec, ProviderError
from apps.integrations.llm import (
    UNTRUSTED_SYSTEM_NOTE,
    pydantic_schema,
    structured_call,
    wrap_untrusted,
)
from apps.integrations.llm.errors import StructuredOutputError
from apps.research.buyers import RankedPerson, Ranking
from apps.research.engine.access import AnalysisUnavailable, get_llm_adapter
from apps.research.engine.rules_compiler import compile_rules

logger = logging.getLogger(__name__)

PROMPT_NAME = "buyer_choice"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"
ATTEMPTS = 2


class BuyerChoice(BaseModel):
    """What the model returns. Contact ids are the ones in the prompt, as text."""

    model_config = ConfigDict(extra="forbid")

    primary_contact_id: str = Field(max_length=64)
    secondary_contact_id: str | None = Field(default=None, max_length=64)
    rationale: str = Field(min_length=1, max_length=800)
    concerns: list[str] = Field(default_factory=list, max_length=4)


@dataclass
class Explained:
    """The ranking to store (possibly with a tie broken), its explanation and the model used."""

    ranking: Ranking
    explanation: dict[str, Any]
    model_name: str = ""
    prompt_version: str = ""
    schema_version: str = ""


def _top_candidates(ranking: Ranking) -> tuple[list[RankedPerson], bool]:
    """The people who share the top score, and whether that is a tie (two or more)."""
    useful = [p for p in ranking.people if p.score > 0]
    if not useful:
        return [], False
    tied = [p for p in useful if p.score == useful[0].score]
    return tied, len(tied) > 1


def rules_explanation(ranking: Ranking, note: str = "") -> dict[str, Any]:
    """The explanation without a model: the rule reasons of the first person, in words."""
    useful = [p for p in ranking.people if p.score > 0]
    if useful:
        first = useful[0]
        rationale = f"{first.name} ({first.title or 'no title'}) ranks first. " + " ".join(
            first.reasons
        )
    else:
        rationale = "No one matches the campaign's preferred titles or target departments."
    return {
        "source": "rules",
        "primary_contact_id": str(useful[0].contact_id) if useful else None,
        "secondary_contact_id": str(useful[1].contact_id) if len(useful) > 1 else None,
        "rationale": rationale,
        "concerns": [],
        "tie_broken": False,
        "note": note,
    }


def buyer_prompt(
    company: Company, ranking: Ranking, tied: list[RankedPerson], tie: bool
) -> PromptSpec:
    rules = compile_rules(company.campaign.current_profile).text
    lines = []
    for person in ranking.people:
        lines.append(
            f"- id {person.contact_id} | {person.name} | {person.title or 'no title'} | "
            f"rule rank {person.rank} | " + " ".join(person.reasons)
        )
    allowed = ", ".join(str(p.contact_id) for p in tied)
    if tie:
        task = (
            "Several people score the same at the top, so choose the best primary target "
            f"among exactly these ids: {allowed}. Pick a secondary target from any candidate."
        )
    else:
        task = (
            f"The rules already chose the primary target: id {allowed}. Return that id as "
            "primary_contact_id and explain why. Pick a secondary target from the other "
            "candidates, or null."
        )
    system = (
        "You explain who to contact first at one company for an outbound campaign. Use only the "
        "campaign rules and the candidates given. Never invent a person, a title or a fact, and "
        "never return an id that is not in the list. `rationale` is one to three plain "
        "sentences. `concerns` lists real doubts (for example a title that only partly matches), "
        "or is empty. " + UNTRUSTED_SYSTEM_NOTE
    )
    user = (
        f"Campaign rules:\n{rules}\n\nCompany: {company.name}\n"
        f"{ranking.note}\n\nTask: {task}\n\n"
        + wrap_untrusted("Candidates:\n" + "\n".join(lines), "buyer_candidates")
    )
    return PromptSpec(name=PROMPT_NAME, version=PROMPT_VERSION, system=system, user=user)


def _problem(choice: BuyerChoice, allowed_primary: set[str], candidates: set[str]) -> str | None:
    if choice.primary_contact_id not in allowed_primary:
        return "primary_contact_id is not one of the allowed candidates"
    secondary = choice.secondary_contact_id
    if secondary is not None and (
        secondary not in candidates or secondary == choice.primary_contact_id
    ):
        return "secondary_contact_id is not another candidate"
    return None


def _renumber(people: list[RankedPerson]) -> None:
    for position, person in enumerate(people, start=1):
        person.rank = position
        person.suggested_role = ContactRole.NONE
    useful = [p for p in people if p.score > 0]
    if useful:
        useful[0].suggested_role = ContactRole.PRIMARY
    if len(useful) > 1:
        useful[1].suggested_role = ContactRole.SECONDARY


def _apply_choice(ranking: Ranking, choice: BuyerChoice, tie: bool) -> Ranking:
    """Put the chosen primary first (only matters in a tie) and the chosen secondary second."""
    people = list(ranking.people)
    chosen = next(p for p in people if str(p.contact_id) == choice.primary_contact_id)
    if tie and people[0] is not chosen:
        people.remove(chosen)
        people.insert(0, chosen)
        chosen.reasons.append("Chosen by the AI model among candidates the rules ranked equally.")
    if choice.secondary_contact_id is not None:
        second = next(p for p in people if str(p.contact_id) == choice.secondary_contact_id)
        if second is not people[0] and second.score > 0:
            people.remove(second)
            people.insert(1, second)
    _renumber(people)
    return Ranking(ranking.note, people)


def explain_ranking(company: Company, ranking: Ranking, user: Any = None) -> Explained:
    """The ranking with an AI explanation, or with the rules' own when the model cannot be used."""
    tied, tie = _top_candidates(ranking)
    if not tied:
        return Explained(ranking, rules_explanation(ranking, "No candidate to explain."))
    try:
        adapter = get_llm_adapter(company.client_id, campaign=company.campaign, user=user)
    except AnalysisUnavailable as exc:
        return Explained(ranking, rules_explanation(ranking, str(exc)))
    candidates = {str(p.contact_id) for p in ranking.people}
    allowed = {str(p.contact_id) for p in tied}
    prompt = buyer_prompt(company, ranking, tied, tie)
    note = "The model's answer was not usable twice."
    for _ in range(ATTEMPTS):
        try:
            result = structured_call(adapter, prompt, pydantic_schema(BuyerChoice), max_tokens=700)
        except (ProviderError, StructuredOutputError) as error:
            logger.warning(
                "Buyer explanation failed: company=%s error=%s", company.pk, type(error).__name__
            )
            note = "The AI model could not be used for this run."
            break
        choice = result.data
        if choice is None:
            note = "The AI model returned nothing usable."
            break
        problem = _problem(choice, allowed, candidates)
        if problem is not None:
            logger.warning("Buyer explanation refused: company=%s problem=%s", company.pk, problem)
            continue
        chosen = _apply_choice(ranking, choice, tie)
        model = (
            result.usage.model or str(getattr(adapter, "config", {}).get("model", "")) or "unknown"
        )
        return Explained(
            chosen,
            {
                "source": "model",
                "primary_contact_id": choice.primary_contact_id,
                "secondary_contact_id": choice.secondary_contact_id,
                "rationale": choice.rationale,
                "concerns": list(choice.concerns),
                "tie_broken": tie,
                "tied_contact_ids": sorted(allowed) if tie else [],
                "note": "",
            },
            model_name=model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
        )
    return Explained(ranking, rules_explanation(ranking, note))
