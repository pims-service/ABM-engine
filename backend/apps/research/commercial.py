"""Commercial team numbers entered by hand (Brief section 5: headcount and growth).

Until a company-data provider is connected, a person can type what they found (for example on the
company's public page): current headcounts per team and, if known, the same numbers 3, 6 and 12
months ago. The numbers become a new, immutable research snapshot whose source records who typed
them, what they said it came from and the date, and the growth calculator (issue #97) turns the
history into the 3, 6 and 12 month changes. Nothing is estimated: a window without data stays
empty.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies.models import Company, CompanyResearch, DataSourceType
from apps.companies.services import add_research_snapshot, create_data_source
from apps.outreach.models import ActivityType
from apps.outreach.services import record_activity

from .growth import HeadcountPoint, growth_from_points, shift_months, to_research_fields

#: request field -> (growth department key, CompanyResearch column or None for the whole company)
TEAMS: dict[str, tuple[str | None, str]] = {
    "employees": (None, "employee_count"),
    "sales": ("sales", "sales_headcount"),
    "bd": ("bd", "bd_headcount"),
    "marketing": ("marketing", "marketing_headcount"),
    "commercial": ("commercial", "commercial_partnerships_headcount"),
}
CARRIED = (
    "industry",
    "description",
    "headquarters",
    "business_model",
    "classification",
    "products_services",
    "target_customers",
)
CARRIED_NUMBERS = (
    "employee_count",
    "sales_headcount",
    "bd_headcount",
    "marketing_headcount",
    "commercial_partnerships_headcount",
    "department_growth",
    "headcount_change_3m",
    "headcount_change_6m",
    "headcount_change_12m",
)


def _count() -> serializers.IntegerField:
    return serializers.IntegerField(required=False, min_value=0, max_value=10_000_000)


class HistoryEntrySerializer(serializers.Serializer[Any]):
    months_ago = serializers.ChoiceField(choices=[3, 6, 12])
    employees = _count()
    sales = _count()
    bd = _count()
    marketing = _count()
    commercial = _count()


class CommercialTeamRequestSerializer(serializers.Serializer[Any]):
    source_note = serializers.CharField(
        max_length=200,
        help_text="Where the numbers come from, for example 'LinkedIn company page'.",
    )
    source_url = serializers.URLField(required=False, allow_blank=True, max_length=2000)
    as_of = serializers.DateField(
        required=False, help_text="The date the numbers were true (default: today)."
    )
    employees = _count()
    sales = _count()
    bd = _count()
    marketing = _count()
    commercial = _count()
    history = HistoryEntrySerializer(many=True, required=False)

    def validate_history(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > 3:
            raise serializers.ValidationError("At most three earlier periods (3, 6 and 12 months).")
        return value


def _points(when: date, source: str, values: dict[str, Any]) -> list[HeadcountPoint]:
    return [
        HeadcountPoint(date=when, count=int(values[field]), source=source, department=dept)
        for field, (dept, _) in TEAMS.items()
        if values.get(field) is not None
    ]


def record_commercial_team(company: Company, data: dict[str, Any], user: User) -> CompanyResearch:
    """Save the numbers as a new snapshot (see the module docstring)."""
    as_of = data.get("as_of") or timezone.now().date()
    if as_of > timezone.now().date():
        raise ValidationError({"as_of": ["The date cannot be in the future."]})
    if not any(data.get(field) is not None for field in TEAMS):
        raise ValidationError({"non_field_errors": ["Enter at least one number."]})
    try:
        source = create_data_source(
            company.campaign.client,
            DataSourceType.MANUAL,
            data["source_note"],
            url=data.get("source_url", ""),
            evidence_date=as_of,
            user=user,
        )
        points = _points(as_of, str(source.pk), data)
        for entry in data.get("history", []):
            when = shift_months(as_of, -int(entry["months_ago"]))
            points += _points(when, str(source.pk), entry)
        report = growth_from_points(points, as_of)
        grown = to_research_fields(report)
        previous = CompanyResearch.objects.latest_for(company)
        facts: dict[str, Any] = {}
        if previous is not None:
            for name in (*CARRIED, *CARRIED_NUMBERS):
                facts[name] = getattr(previous, name)
        for field, (_, column) in TEAMS.items():
            if data.get(field) is not None:
                facts[column] = int(data[field])
        for name, value in grown.items():
            if value is not None:
                facts[name] = value
        snapshot = add_research_snapshot(company, source, **facts)
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise ValidationError(exc.message_dict) from exc
        raise ValidationError({"non_field_errors": exc.messages}) from exc
    record_activity(
        company,
        ActivityType.RESEARCHED,
        user,
        source="entered by hand",
        note=data["source_note"],
    )
    return snapshot
