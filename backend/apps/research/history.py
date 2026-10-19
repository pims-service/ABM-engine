"""Research history: every snapshot of a company and what changed between them (Brief section 19).

Snapshots are never edited, so a re-run keeps the old ones. The diff of a snapshot is against the
one just before it. A field that was not found in either snapshot is not a change.
"""

from __future__ import annotations

from typing import Any

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
            changes.append({"field": field, "label": label, "old": before, "new": after})
    return changes


def history_for(company: Company) -> list[dict[str, Any]]:
    """Newest first, each with its source and its changes against the snapshot before it."""
    rows = list(
        CompanyResearch.objects.filter(company=company)
        .latest_first()
        .select_related("data_source")[: MAX_SNAPSHOTS + 1]
    )
    body: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:MAX_SNAPSHOTS]):
        older = rows[index + 1] if index + 1 < len(rows) else None
        body.append(
            {
                "id": row.pk,
                "researched_at": row.researched_at,
                "source": row.data_source.name,
                "first": older is None,
                "changes": diff(older, row),
            }
        )
    return body


class FieldChangeSerializer(serializers.Serializer[Any]):
    field = serializers.CharField()
    label = serializers.CharField()
    old = serializers.CharField(allow_null=True, help_text="Null: it was not found before.")
    new = serializers.CharField(allow_null=True, help_text="Null: it is no longer found.")


class SnapshotSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    researched_at = serializers.DateTimeField()
    source = serializers.CharField()
    first = serializers.BooleanField(help_text="The oldest snapshot: nothing to compare with.")
    changes = FieldChangeSerializer(many=True)
