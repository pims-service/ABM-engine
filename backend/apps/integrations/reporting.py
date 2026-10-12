"""Aggregation of ``ProviderCall`` rows (issue #79): usage per client, provider, day, month, run.

Money is never added across currencies: every grouping also splits by ``currency``. Credits are
the provider's own unit, so summing them across providers is only meaningful per provider (group
by ``provider`` or ``connection``). All numbers come from the logged rows, nothing is estimated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC
from decimal import Decimal
from typing import Any

from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import TruncDate, TruncMonth

from apps.campaigns.models import Campaign
from apps.core.models import Job

from .models import ProviderCall

MAX_GROUPS = 1000

#: ``group_by`` name -> (annotation or column used for grouping)
GROUPS: dict[str, str] = {
    "provider": "provider",
    "category": "category",
    "operation": "operation",
    "connection": "connection_id",
    "campaign": "campaign_id",
    "job": "job_id",
    "status": "status",
    "model": "model",
    "day": "day",
    "month": "month",
}
DEFAULT_GROUP = ("provider",)

_AGGREGATES: dict[str, Any] = {
    "calls": Count("id"),
    "errors": Count("id", filter=Q(status="error")),
    "cache_hits": Count("id", filter=Q(cache_hit=True)),
    "credits": Sum("credits"),
    "cost": Sum("cost"),
    "input_tokens": Sum("input_tokens"),
    "output_tokens": Sum("output_tokens"),
}


def _row(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for key in ("day", "month"):
        if key in out and out[key] is not None:
            out[key] = out[key].isoformat()
    for key in ("campaign_id", "connection_id", "job_id"):
        if key in out and out[key] is not None:
            out[key] = str(out[key])
    return out


def summarize(queryset: QuerySet[ProviderCall], group_by: Sequence[str]) -> list[dict[str, Any]]:
    """One dict per distinct combination of ``group_by`` (names from ``GROUPS``) and currency,
    with ``calls``, ``errors``, ``cache_hits``, ``credits``, ``cost``, ``input_tokens`` and
    ``output_tokens``. Ordered by the group keys; at most ``MAX_GROUPS`` rows."""
    unknown = [g for g in group_by if g not in GROUPS]
    if unknown:
        raise ValueError(f"unknown group_by: {unknown}")
    annotated = queryset.annotate(
        day=TruncDate("created_at", tzinfo=UTC), month=TruncMonth("created_at", tzinfo=UTC)
    )
    keys = [GROUPS[g] for g in dict.fromkeys(group_by)]
    rows = (
        annotated.order_by()
        .values(*keys, "currency")
        .annotate(**_AGGREGATES)
        .order_by(*keys, "currency")
    )
    return [_row(r) for r in rows[:MAX_GROUPS]]


def totals(queryset: QuerySet[ProviderCall]) -> list[dict[str, Any]]:
    """The grand totals, one row per currency (a single row with ``currency=""`` when no cost)."""
    rows = queryset.order_by().values("currency").annotate(**_AGGREGATES).order_by("currency")
    return [_row(r) for r in rows]


def run_totals(*, campaign: Campaign | None = None, job: Job | None = None) -> list[dict[str, Any]]:
    """What a campaign (all its calls) or one job run cost, per currency."""
    if (campaign is None) == (job is None):
        raise ValueError("Pass exactly one of campaign or job.")
    queryset = (
        ProviderCall.objects.filter(campaign=campaign)
        if campaign is not None
        else ProviderCall.objects.filter(job=job)
    )
    return totals(queryset)


def cost_per_accepted_account(
    cost: Decimal | None, accepted_accounts: int | None
) -> Decimal | None:
    """Placeholder for the headline metric: total cost divided by accounts a human accepted.

    The accepted count comes from the review flow (M7/M8) and is passed in; ``None`` when either
    number is unknown or no account was accepted (never a division by zero, never a made-up 0).
    """
    if cost is None or not accepted_accounts or accepted_accounts < 0:
        return None
    return cost / Decimal(accepted_accounts)


def costs_by_currency(rows: Iterable[dict[str, Any]]) -> dict[str, Decimal]:
    """``{currency: total cost}`` from ``summarize``/``totals`` rows (rows without cost skipped)."""
    out: dict[str, Decimal] = {}
    for row in rows:
        if row.get("cost") is not None:
            out[row["currency"]] = out.get(row["currency"], Decimal(0)) + row["cost"]
    return out
