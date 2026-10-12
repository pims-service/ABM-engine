"""Serializers for the usage and budget endpoints (issue #79). Read-only except the budget PUT.

Nothing here can carry a request or response body or a secret: ``ProviderCall`` has none.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.integrations.models import CallStatus, IntegrationCategory, ProviderCall
from apps.integrations.reporting import GROUPS


class ProviderCallSerializer(serializers.ModelSerializer[ProviderCall]):
    has_raw_payload = serializers.BooleanField(
        read_only=True,
        help_text="True while a raw payload is still retained for this call. The payload itself "
        "is never served by the API.",
    )

    class Meta:
        model = ProviderCall
        fields = (
            "id",
            "client",
            "connection",
            "campaign",
            "job",
            "triggered_by",
            "category",
            "provider",
            "operation",
            "status",
            "error_code",
            "latency_ms",
            "credits",
            "cost",
            "currency",
            "input_tokens",
            "output_tokens",
            "model",
            "cache_hit",
            "request_id",
            "has_raw_payload",
            "created_at",
        )
        read_only_fields = fields


class UsageQuerySerializer(serializers.Serializer[dict[str, Any]]):
    """Filters shared by the list and the summary (validated by the view; OpenAPI docs)."""

    client = serializers.UUIDField(required=False, help_text="Only this client's calls.")
    connection = serializers.UUIDField(required=False)
    campaign = serializers.UUIDField(required=False)
    job = serializers.UUIDField(required=False)
    category = serializers.ChoiceField(choices=IntegrationCategory.choices, required=False)
    provider = serializers.CharField(required=False, max_length=64)
    operation = serializers.CharField(required=False, max_length=64)
    status = serializers.ChoiceField(choices=CallStatus.choices, required=False)
    cache_hit = serializers.BooleanField(required=False)
    date_from = serializers.DateField(
        required=False, help_text="First day (UTC, inclusive) of the range."
    )
    date_to = serializers.DateField(
        required=False, help_text="Last day (UTC, inclusive) of the range."
    )


class UsageSummaryQuerySerializer(UsageQuerySerializer):
    group_by = serializers.CharField(
        required=False,
        help_text="Comma-separated grouping, any of: " + ", ".join(GROUPS) + ". Default: provider. "
        "Money is always split by `currency` as well.",
    )


class UsageRowSerializer(serializers.Serializer[dict[str, Any]]):
    """One group of the summary: the requested group keys (only those) plus the numbers."""

    provider = serializers.CharField(required=False)
    category = serializers.CharField(required=False)
    operation = serializers.CharField(required=False)
    connection_id = serializers.UUIDField(required=False)
    campaign_id = serializers.UUIDField(required=False, allow_null=True)
    job_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.CharField(required=False)
    model = serializers.CharField(required=False)
    day = serializers.DateField(required=False)
    month = serializers.DateField(required=False)
    currency = serializers.CharField(help_text="ISO 4217 code of `cost`; empty when no cost.")
    calls = serializers.IntegerField()
    errors = serializers.IntegerField()
    cache_hits = serializers.IntegerField()
    credits = serializers.DecimalField(max_digits=24, decimal_places=4, allow_null=True)
    cost = serializers.DecimalField(max_digits=24, decimal_places=6, allow_null=True)
    input_tokens = serializers.IntegerField(allow_null=True)
    output_tokens = serializers.IntegerField(allow_null=True)


class UsageSummarySerializer(serializers.Serializer[dict[str, Any]]):
    group_by = serializers.ListField(child=serializers.CharField())
    totals = UsageRowSerializer(many=True, help_text="Grand totals, one row per currency.")
    results = UsageRowSerializer(many=True, help_text="At most 1000 groups.")


class BudgetSerializer(serializers.Serializer[dict[str, Any]]):
    """The budget of a connection and where this month stands against it."""

    connection = serializers.UUIDField()
    configured = serializers.BooleanField(help_text="False when no budget row exists yet.")
    credit_limit = serializers.DecimalField(max_digits=18, decimal_places=4, allow_null=True)
    cost_limit = serializers.DecimalField(max_digits=18, decimal_places=6, allow_null=True)
    currency = serializers.CharField()
    warn_at_percent = serializers.IntegerField()
    hard_stop = serializers.BooleanField()
    period_start = serializers.DateTimeField(help_text="Start of the UTC calendar month.")
    period_end = serializers.DateTimeField()
    used_credits = serializers.DecimalField(max_digits=24, decimal_places=4)
    used_cost = serializers.DecimalField(max_digits=24, decimal_places=6)
    state = serializers.ChoiceField(
        choices=["allow", "warn", "deny"],
        help_text="What the next call would get: `deny` only with `hard_stop` at a reached limit.",
    )
    updated_at = serializers.DateTimeField(allow_null=True)


class BudgetWriteSerializer(serializers.Serializer[dict[str, Any]]):
    """Body of the budget PUT: a full replacement (omitted limits mean no cap)."""

    credit_limit = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, required=False, allow_null=True
    )
    cost_limit = serializers.DecimalField(
        max_digits=18, decimal_places=6, min_value=0, required=False, allow_null=True
    )
    currency = serializers.CharField(
        max_length=3, required=False, allow_blank=True, help_text="Required with `cost_limit`."
    )
    warn_at_percent = serializers.IntegerField(min_value=1, max_value=100, default=80)
    hard_stop = serializers.BooleanField(
        default=True, help_text="False: reaching a limit only warns, calls are never blocked."
    )
