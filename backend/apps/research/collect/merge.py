"""One merged profile from several snapshots (issue #93: "precedence when sources disagree").

Snapshots are append-only and each belongs to one source, so a company can hold a website
snapshot and a provider snapshot that disagree. ``merged_profile`` picks, per field, the value of
the best kind of source (``settings.RESEARCH_SOURCE_PRECEDENCE``; inside a kind the newest
snapshot wins) and lists the values it did not pick as alternatives, each with its source. Nothing
is stored: the snapshots stay as they are.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from apps.companies.models import Company, CompanyResearch
from apps.research.history import FIELDS


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _source(snapshot: CompanyResearch) -> dict[str, Any]:
    source = snapshot.data_source
    return {
        "id": str(source.pk),
        "name": source.name,
        "type": source.type,
        "researched_at": snapshot.researched_at,
    }


def merged_profile(company: Company) -> list[dict[str, Any]]:
    """For each field found in any snapshot: the winning value, its source and the alternatives."""
    precedence = list(getattr(settings, "RESEARCH_SOURCE_PRECEDENCE", ()))

    def rank(snapshot: CompanyResearch) -> int:
        kind = snapshot.data_source.type
        return precedence.index(kind) if kind in precedence else len(precedence)

    snapshots = list(
        CompanyResearch.objects.filter(company=company).latest_first().select_related("data_source")
    )
    merged: list[dict[str, Any]] = []
    for field, label in FIELDS:
        found = [(s, _text(getattr(s, field))) for s in snapshots]
        found = [(s, v) for s, v in found if v is not None]
        if not found:
            continue
        found.sort(key=lambda pair: rank(pair[0]))  # stable: newest first inside a kind
        winner, value = found[0]
        seen = {value}
        alternatives = []
        for snapshot, other in found[1:]:
            if other in seen:
                continue
            seen.add(other)
            alternatives.append({"value": other, "origin": _source(snapshot)})
        merged.append(
            {
                "field": field,
                "name": label,
                "value": value,
                "origin": _source(winner),
                "alternatives": alternatives,
            }
        )
    return merged
