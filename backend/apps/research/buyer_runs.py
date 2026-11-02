"""Stored buyer ranking runs: re-run, staleness and history (issue #137, Brief sections 8 and 19).

``apps.research.buyers`` ranks the people at a company from rules, live. A **run** stores that
ranking at a moment, with a hash of what it was made from, so:

* a re-run makes a new run and keeps the old ones (they are append-only);
* a run is **stale** when its inputs changed since: the campaign's preferred titles or target
  departments, the company size, or the people. Staleness is computed by comparing hashes when
  the runs are read, so a rule change marks every existing ranking stale without touching a row;
* running again with unchanged inputs does nothing and returns the latest run, unless ``force``.

``explain`` adds the AI explanation and tie-break (``apps.research.buyer_explain``). Applying a
ranking to the contacts (primary and secondary roles) is still the separate, human step of
``buyers.apply_ranking``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from rest_framework import serializers

from apps.accounts.models import User
from apps.companies.models import Company, CompanyResearch, Contact
from apps.research.buyer_explain import explain_ranking
from apps.research.buyers import RankedPersonSerializer, Ranking, rank_people, ranking_body
from apps.research.models import BuyerRankingRun

ALGORITHM_VERSION = "1"
CREATED = "created"
UNCHANGED = "unchanged"
MAX_RUNS = 20


def current_inputs(company: Company) -> dict[str, Any]:
    """Everything the ranking of ``company`` depends on, in a stable form."""
    profile = company.campaign.current_profile
    research = CompanyResearch.objects.latest_for(company)
    contacts = Contact.objects.active().filter(company=company)
    return {
        "algorithm": ALGORITHM_VERSION,
        "preferred_buyer_titles": list(profile.preferred_buyer_titles),
        "target_departments": list(profile.target_departments),
        "employees": research.employee_count if research is not None else None,
        "people": sorted([str(c.pk), c.name, c.title] for c in contacts),
    }


def inputs_hash(inputs: dict[str, Any]) -> str:
    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ranking_from(inputs: dict[str, Any]) -> Ranking:
    return rank_people(
        [(person_id, name, title) for person_id, name, title in inputs["people"]],
        preferred=list(inputs["preferred_buyer_titles"]),
        departments=list(inputs["target_departments"]),
        employees=inputs["employees"],
    )


@dataclass(frozen=True)
class RunResult:
    run: BuyerRankingRun
    created: bool
    reason: str  # "created" or "unchanged"


def run_ranking(
    company: Company,
    user: User | None = None,
    *,
    force: bool = False,
    explain: bool = False,
) -> RunResult:
    """Run the buyer ranking of ``company`` and store it, unless nothing changed.

    Unchanged inputs return the latest run (``created`` False) unless ``force``. With ``explain``
    a latest run that has no AI explanation does not count as up to date, so asking for the
    explanation makes a run. Raises ``ValidationError`` for an archived company.
    """
    if company.is_archived:
        raise ValidationError("Archived companies cannot be ranked; restore the company first.")
    inputs = current_inputs(company)
    digest = inputs_hash(inputs)
    latest = BuyerRankingRun.objects.latest_for(company)
    if latest is not None and latest.inputs_hash == digest and not force:
        explained = (latest.explanation or {}).get("source") == "model"
        if not explain or explained:
            return RunResult(latest, False, UNCHANGED)
    ranking = ranking_from(inputs)
    extra: dict[str, Any] = {}
    explanation: dict[str, Any] | None = None
    if explain:
        outcome = explain_ranking(company, ranking, user)
        ranking, explanation = outcome.ranking, outcome.explanation
        extra = {
            "model_name": outcome.model_name,
            "prompt_version": outcome.prompt_version,
            "schema_version": outcome.schema_version,
        }
    body = ranking_body(ranking)
    run = BuyerRankingRun.objects.create(
        company=company,
        inputs_hash=digest,
        algorithm_version=ALGORITHM_VERSION,
        note=ranking.note,
        people=[{**person, "contact_id": str(person["contact_id"])} for person in body["people"]],
        explanation=explanation,
        created_by=user,
        **extra,
    )
    return RunResult(run, True, CREATED)


def describe_runs(company: Company, limit: int = MAX_RUNS) -> list[dict[str, Any]]:
    """The company's runs, newest first, each with its status: ``current`` (the newest, and its
    inputs are unchanged), ``stale`` (the newest, but the inputs changed since) or
    ``superseded`` (an older run)."""
    runs = list(BuyerRankingRun.objects.filter(company=company).latest_first()[:limit])
    digest = inputs_hash(current_inputs(company)) if runs else ""
    body = []
    for index, run in enumerate(runs):
        current = "current" if run.inputs_hash == digest else "stale"
        status = "superseded" if index > 0 else current
        body.append(run_body(run, status))
    return body


def run_body(run: BuyerRankingRun, status: str) -> dict[str, Any]:
    return {
        "id": run.pk,
        "created_at": run.created_at,
        "status": status,
        "note": run.note,
        "people": run.people,
        "explanation": run.explanation,
        "model_name": run.model_name,
        "algorithm_version": run.algorithm_version,
    }


class BuyerRunSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    status = serializers.CharField(
        help_text="`current`, `stale` (the inputs changed since this run) or `superseded` "
        "(a newer run exists)."
    )
    note = serializers.CharField(allow_blank=True)
    people = RankedPersonSerializer(many=True)
    explanation = serializers.JSONField(
        allow_null=True,
        help_text="Only when the run asked for it: `source` (`model` or `rules`), the primary "
        "and secondary contact ids, `rationale`, `concerns`, `tie_broken` and a `note` saying "
        "why the model was not used.",
    )
    model_name = serializers.CharField(allow_blank=True)
    algorithm_version = serializers.CharField()


class BuyerRunRequestSerializer(serializers.Serializer[Any]):
    force = serializers.BooleanField(
        default=False, help_text="Make a new run even if nothing changed."
    )
    explain = serializers.BooleanField(
        default=False,
        help_text="Add the AI explanation and tie-break. Needs an AI connection; without one the "
        "run keeps the rules ranking with a plain explanation.",
    )


class BuyerRunResultSerializer(serializers.Serializer[Any]):
    run = BuyerRunSerializer()
    created = serializers.BooleanField(
        help_text="False: nothing changed, the latest run is returned."
    )
    reason = serializers.CharField(help_text="`created` or `unchanged`.")


class BulkBuyerRunRequestSerializer(BuyerRunRequestSerializer):
    company_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=200, allow_empty=False
    )


class BulkBuyerRunItemSerializer(serializers.Serializer[Any]):
    company_id = serializers.UUIDField()
    status = serializers.CharField(help_text="`created`, `unchanged` or `error`.")
    run_id = serializers.UUIDField(allow_null=True)
    error = serializers.CharField(allow_blank=True)
