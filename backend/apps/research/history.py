"""Research history: every snapshot of a company and what changed between them (Brief section 19).

Snapshots are never edited, so a re-run keeps the old ones. The diff of a snapshot is against the
one just before it. A field that was not found in either snapshot is not a change.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.companies.models import Company, CompanyResearch

MAX_SNAPSHOTS = 20

FIELDS: tuple[tuple[str, str], ...] = (
    ("industry", "Industry"),
    ("description", "Description"),
    ("headquarters", "Headquarters"),
    ("employee_count", "Employees"),
    ("business_model", "Business model"),
    ("classification", "B2B or B2C"),
    ("products_services", "Products and services"),
    ("target_customers", "Target customers"),
    ("sales_headcount", "Sales headcount"),
    ("bd_headcount", "Business development headcount"),
    ("marketing_headcount", "Marketing headcount"),
    ("commercial_partnerships_headcount", "Commercial and partnerships headcount"),
    ("headcount_change_3m", "Headcount change, 3 months (%)"),
    ("headcount_change_6m", "Headcount change, 6 months (%)"),
    ("headcount_change_12m", "Headcount change, 12 months (%)"),
)


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def diff(old: CompanyResearch | None, new: CompanyResearch) -> list[dict[str, Any]]:
    """The changed fields of ``new`` against ``old`` (``old`` None: everything found is new)."""
    changes: list[dict[str, Any]] = []
    for field, label in FIELDS:
        before = _text(getattr(old, field)) if old is not None else None
        after = _text(getattr(new, field))
        if before != after:
            changes.append({"field": field, "name": label, "old": before, "new": after})
    return changes


def history_for(company: Company) -> list[dict[str, Any]]:
    """Newest first, each with its source and its changes against the snapshot before it.

    ``settings.RESEARCH_HISTORY_DAYS`` (0: no limit) hides snapshots older than that many days,
    except the newest one. It only limits what is listed: snapshots are append-only and are never
    deleted. The oldest listed snapshot is still compared with the one before it, so what it
    changed stays right; ``first`` is true only for the company's very first snapshot.
    """
    rows = list(
        CompanyResearch.objects.filter(company=company)
        .latest_first()
        .select_related("data_source")[: MAX_SNAPSHOTS + 1]
    )
    shown = rows[:MAX_SNAPSHOTS]
    days = int(getattr(settings, "RESEARCH_HISTORY_DAYS", 0))
    if days > 0:
        cutoff = timezone.now() - timedelta(days=days)
        shown = [r for i, r in enumerate(shown) if i == 0 or r.researched_at >= cutoff]
    body: list[dict[str, Any]] = []
    for index, row in enumerate(shown):
        older = rows[index + 1] if index + 1 < len(rows) else None
        body.append(
            {
                "id": row.pk,
                "researched_at": row.researched_at,
                "source_name": row.data_source.name,
                "first": older is None,
                "changes": diff(older, row),
            }
        )
    return body


def diff_between(company: Company, older_id: Any, newer_id: Any) -> dict[str, Any] | None:
    """What changed from snapshot ``older_id`` to snapshot ``newer_id`` of ``company`` (any two,
    not only neighbours), or ``None`` when either is not a snapshot of this company."""
    found = {
        str(row.pk): row
        for row in CompanyResearch.objects.filter(
            company=company, pk__in=[older_id, newer_id]
        ).select_related("data_source")
    }
    older, newer = found.get(str(older_id)), found.get(str(newer_id))
    if older is None or newer is None:
        return None
    return {
        "from_id": older.pk,
        "to_id": newer.pk,
        "from_researched_at": older.researched_at,
        "to_researched_at": newer.researched_at,
        "from_source_name": older.data_source.name,
        "to_source_name": newer.data_source.name,
        "changes": diff(older, newer),
    }


class FieldChangeSerializer(serializers.Serializer[Any]):
    field = serializers.CharField()
    name = serializers.CharField(help_text="What changed, in words.")
    old = serializers.CharField(allow_null=True, help_text="Null: it was not found before.")
    new = serializers.CharField(allow_null=True, help_text="Null: it is no longer found.")


class SnapshotSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    researched_at = serializers.DateTimeField()
    source_name = serializers.CharField()
    first = serializers.BooleanField(help_text="The oldest snapshot: nothing to compare with.")
    changes = FieldChangeSerializer(many=True)


class SnapshotDiffSerializer(serializers.Serializer[Any]):
    from_id = serializers.UUIDField()
    to_id = serializers.UUIDField()
    from_researched_at = serializers.DateTimeField()
    to_researched_at = serializers.DateTimeField()
    from_source_name = serializers.CharField()
    to_source_name = serializers.CharField()
    changes = FieldChangeSerializer(many=True)
