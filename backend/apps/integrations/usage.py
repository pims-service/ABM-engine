"""Usage tracking, budgets and the tracked wrapper around ``execute`` (issue #79).

Callers (jobs, services, views) run adapter operations through ``tracked_execute`` instead of
``execute``::

    result = tracked_execute(connection, adapter, adapter.enrich_company, ref, job=job)

It checks the connection's budget BEFORE the call (``BudgetExceeded``, a
``ProviderQuotaExceeded``, on a hard stop), runs the framework ``execute`` (so the contract and the
typed errors are unchanged) and writes exactly one ``ProviderCall`` row per call: success, failure
and refusal. A cache hit (``caching.cached_execute``) writes one row with ``cache_hit=True`` and
zero cost. Writing the row can never break the call: a failure to log is logged, swallowed and
counted (``logging_failure_count``).

Rows hold no request or response body and no secret, only what ``Usage`` and ``Provenance`` carry.
Full guide: ``docs/integrations/usage-and-cache.md``.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.core.audit import record_change, snapshot
from apps.core.models import Job

from .cache_policy import CachePolicy, effective_policy
from .framework import (
    Adapter,
    AdapterResult,
    ProviderError,
    ProviderQuotaExceeded,
    Usage,
    execute,
)
from .framework.envelope import Provenance
from .models import CallStatus, IntegrationConnection, ProviderCall, RawPayload, UsageBudget
from .payloads import RAW_REF_PREFIX, collecting_raw, redact_payload, size_of

logger = logging.getLogger(__name__)
budget_log = logging.getLogger("apps.integrations.budget")

BUDGET_EXCEEDED_CODE = "budget_exceeded"
BUDGET_AUDIT_TYPE = "usage_budget"
BUDGET_AUDIT_FIELDS = (
    "credit_limit",
    "cost_limit",
    "currency",
    "warn_at_percent",
    "hard_stop",
)
CREDIT_LIMIT_MAX = Decimal("99999999999999.9999")  # max_digits=18, decimal_places=4
COST_LIMIT_MAX = Decimal("999999999999.999999")  # max_digits=18, decimal_places=6

ALLOW = "allow"
WARN = "warn"
DENY = "deny"


class BudgetExceeded(ProviderQuotaExceeded):
    """The connection's hard budget for this month is used up: no provider request was made.

    A ``ProviderQuotaExceeded`` (so callers that already handle quota errors keep working), with
    its own stable ``code`` to tell it from the provider's own quota error.
    """

    code = BUDGET_EXCEEDED_CODE
    message = "The monthly budget of this integration is used up. Raise the budget or wait."


# ------------------------------------------------------------------ logging failures

_failure_lock = threading.Lock()
_failures = 0


def logging_failure_count() -> int:
    """How many call rows could not be written since the process started (a health signal)."""
    return _failures


def reset_logging_failures() -> None:
    global _failures
    with _failure_lock:
        _failures = 0


def _count_failure() -> None:
    global _failures
    with _failure_lock:
        _failures += 1


# ------------------------------------------------------------------ recording


def record_call(
    connection: IntegrationConnection,
    *,
    operation: str,
    status: str,
    usage: Usage | None = None,
    provenance: Provenance | None = None,
    error_code: str = "",
    cache_hit: bool = False,
    latency_ms: int | None = None,
    job: Job | None = None,
    campaign: Campaign | None = None,
    user: User | None = None,
) -> ProviderCall | None:
    """Write one ``ProviderCall`` row. Never raises: on any failure it logs, counts the failure
    (``logging_failure_count``) and returns ``None``.

    ``usage`` and ``provenance`` come from the ``AdapterResult``; only their numbers and the
    request id are kept. ``campaign`` defaults to the job's campaign. A cache hit is stored with
    zero credits and cost.
    """
    try:
        usage = usage or Usage()
        if campaign is None and job is not None:
            campaign = job.campaign
        if cache_hit:
            credits: Decimal | None = Decimal(0)
            cost: Decimal | None = Decimal(0)
        else:
            credits, cost = usage.credits, usage.cost
        row = ProviderCall(
            connection=connection,
            campaign=campaign,
            job=job,
            triggered_by=user if getattr(user, "pk", None) else None,
            category=connection.category,
            provider=connection.provider,
            operation=str(operation)[:64] or "unknown",
            status=status,
            error_code=error_code[:64] if status == CallStatus.ERROR else "",
            latency_ms=usage.latency_ms if usage.latency_ms is not None else latency_ms,
            credits=credits,
            cost=cost,
            currency=(usage.currency or "") if cost else "",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=usage.model or "",
            cache_hit=cache_hit,
            request_id=(provenance.request_id or "") if provenance else "",
        )
        with transaction.atomic():  # a savepoint: a failed insert must not poison the caller's tx
            row.save()
        return row
    except Exception:
        _count_failure()
        logger.exception(
            "could not record a provider call (provider=%s operation=%s status=%s)",
            getattr(connection, "provider", "?"),
            operation,
            status,
        )
        return None


def store_raw_payload(call: ProviderCall, payload: Any, policy: CachePolicy) -> str | None:
    """Keep ``payload`` (redacted) for ``call`` when the policy allows. Returns the
    ``rawpayload:<id>`` reference, or ``None`` when nothing was stored (policy, size cap, bad
    payload or a failure; never raises)."""
    if not policy.allow_store_raw:
        return None
    try:
        clean = redact_payload(payload)
        size = size_of(clean)
        if size > policy.max_payload_bytes:
            logger.warning(
                "raw payload of %s.%s not kept: %d bytes is over the %d byte cap",
                call.provider,
                call.operation,
                size,
                policy.max_payload_bytes,
            )
            return None
        expires = timezone.now() + timedelta(days=policy.retention_days)
        with transaction.atomic():
            raw = RawPayload.objects.create(
                call=call, payload=clean, size_bytes=size, expires_at=expires
            )
        return f"{RAW_REF_PREFIX}{raw.pk}"
    except Exception:
        _count_failure()
        logger.exception("could not store a raw payload (provider=%s)", call.provider)
        return None


# ------------------------------------------------------------------ budgets


@dataclass(frozen=True)
class BudgetStatus:
    """Where a connection stands against its budget this month (UTC calendar month)."""

    action: str  # ALLOW, WARN or DENY
    period_start: datetime
    period_end: datetime
    used_credits: Decimal
    used_cost: Decimal
    credit_limit: Decimal | None = None
    cost_limit: Decimal | None = None
    currency: str = ""
    warn_at_percent: int = 80
    hard_stop: bool = True
    configured: bool = False
    reason: str = ""  # "credits" or "cost": the measure that decided a WARN or DENY

    @property
    def allowed(self) -> bool:
        return self.action != DENY


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the UTC calendar month containing ``now``."""
    moment = now.astimezone(UTC)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def month_usage(
    connection: IntegrationConnection, currency: str = "", *, now: datetime | None = None
) -> tuple[Decimal, Decimal]:
    """(credits, cost) logged for ``connection`` this month. Cost counts rows in ``currency``
    and rows with no currency (conservative: an unlabeled cost is assumed to be the budget's)."""
    start, end = month_bounds(now or timezone.now())
    totals = ProviderCall.objects.filter(
        connection=connection, created_at__gte=start, created_at__lt=end
    ).aggregate(
        credits=Sum("credits"),
        cost=Sum("cost", filter=Q(currency=currency) | Q(currency="")),
    )
    return totals["credits"] or Decimal(0), totals["cost"] or Decimal(0)


def check_budget(connection: IntegrationConnection, *, now: datetime | None = None) -> BudgetStatus:
    """The budget decision for the NEXT call of ``connection``: ``allow``, ``warn`` or ``deny``.

    * ``deny``: a limit is reached (used >= limit) and the budget has ``hard_stop``;
    * ``warn``: used >= ``warn_at_percent`` of a limit, or a limit is reached without hard stop;
    * ``allow``: otherwise, and always when no budget is set.

    Comparisons are exact ``Decimal`` arithmetic. It is a check, not a reservation: concurrent
    calls may overshoot a limit by what they cost (documented in usage-and-cache.md).
    """
    moment = now or timezone.now()
    start, end = month_bounds(moment)
    budget = UsageBudget.objects.filter(connection=connection).first()
    if budget is None or (budget.credit_limit is None and budget.cost_limit is None):
        return BudgetStatus(
            ALLOW, start, end, Decimal(0), Decimal(0), configured=budget is not None
        )
    credits, cost = month_usage(connection, budget.currency, now=moment)
    action, reason = ALLOW, ""
    checks = (
        ("credits", credits, budget.credit_limit),
        ("cost", cost, budget.cost_limit),
    )
    for measure, used, limit in checks:
        if limit is None:
            continue
        reached = used >= limit
        warn = used * 100 >= limit * budget.warn_at_percent
        if reached and budget.hard_stop:
            action, reason = DENY, measure
            break
        if (reached or warn) and action == ALLOW:
            action, reason = WARN, measure
    return BudgetStatus(
        action,
        start,
        end,
        credits,
        cost,
        credit_limit=budget.credit_limit,
        cost_limit=budget.cost_limit,
        currency=budget.currency,
        warn_at_percent=budget.warn_at_percent,
        hard_stop=budget.hard_stop,
        configured=True,
        reason=reason,
    )


def enforce_budget(
    connection: IntegrationConnection, *, now: datetime | None = None
) -> BudgetStatus:
    """``check_budget``; raises ``BudgetExceeded`` on ``deny``, logs a warning on ``warn``."""
    status = check_budget(connection, now=now)
    if status.action == DENY:
        raise BudgetExceeded(provider_name=connection.provider)
    if status.action == WARN:
        budget_log.warning(
            "integration budget warning: connection=%s provider=%s measure=%s used_credits=%s "
            "used_cost=%s",
            connection.pk,
            connection.provider,
            status.reason,
            status.used_credits,
            status.used_cost,
        )
    return status


def _decimal(name: str, value: object, maximum: Decimal) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValidationError({name: "Must be a number."})
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({name: "Must be a number."}) from None
    if not number.is_finite() or number < 0:
        raise ValidationError({name: "Must be zero or more."})
    exponent = number.as_tuple().exponent
    places = 4 if name == "credit_limit" else 6
    if isinstance(exponent, int) and -exponent > places:
        raise ValidationError({name: f"At most {places} decimal places."})
    if number > maximum:
        raise ValidationError({name: "Too large."})
    return number


def set_budget(
    connection: IntegrationConnection,
    user: User | None,
    *,
    credit_limit: object = None,
    cost_limit: object = None,
    currency: str = "",
    warn_at_percent: int = 80,
    hard_stop: bool = True,
) -> UsageBudget:
    """Create or replace the budget of ``connection`` (audited). Pass no limit to remove a cap.

    Raises ``ValidationError`` (with a field dict) on bad values, and for an archived connection.
    """
    if connection.is_archived:
        raise ValidationError({"non_field_errors": "An archived connection cannot be changed."})
    errors: dict[str, str] = {}
    credits = cost = None
    try:
        credits = _decimal("credit_limit", credit_limit, CREDIT_LIMIT_MAX)
    except ValidationError as exc:
        errors.update({k: v[0] for k, v in exc.message_dict.items()})
    try:
        cost = _decimal("cost_limit", cost_limit, COST_LIMIT_MAX)
    except ValidationError as exc:
        errors.update({k: v[0] for k, v in exc.message_dict.items()})
    code = (currency or "").strip().upper()
    if code and not (len(code) == 3 and code.isalpha() and code.isascii()):
        errors["currency"] = "Use a 3-letter ISO 4217 code such as USD."
    if cost is not None and not code:
        errors.setdefault("currency", "A cost limit needs a currency.")
    if (
        isinstance(warn_at_percent, bool)
        or not isinstance(warn_at_percent, int)
        or not 1 <= warn_at_percent <= 100
    ):
        errors["warn_at_percent"] = "Must be a whole number from 1 to 100."
    if not isinstance(hard_stop, bool):
        errors["hard_stop"] = "Must be true or false."
    if errors:
        raise ValidationError(errors)

    with transaction.atomic():
        locked = IntegrationConnection.objects.select_for_update().get(pk=connection.pk)
        existing = UsageBudget.objects.filter(connection=locked).first()
        before = snapshot(existing, BUDGET_AUDIT_FIELDS) if existing else None
        budget = existing or UsageBudget(connection=locked)
        budget.credit_limit = credits
        budget.cost_limit = cost
        budget.currency = code if (cost is not None or code) else ""
        budget.warn_at_percent = warn_at_percent
        budget.hard_stop = hard_stop
        budget.updated_by = user if getattr(user, "pk", None) else None
        budget.save()
        record_change(
            "update" if existing else "create",
            BUDGET_AUDIT_TYPE,
            budget.pk,
            actor=user,
            client=locked.client_id,
            before=before,
            after=snapshot(budget, BUDGET_AUDIT_FIELDS),
        )
    return budget


# ------------------------------------------------------------------ the tracked wrapper


def _value(category: object) -> str:
    return str(getattr(category, "value", category))


def tracked_execute[T](
    connection: IntegrationConnection,
    adapter: Adapter,
    operation: Callable[..., AdapterResult[T]],
    /,
    *args: Any,
    job: Job | None = None,
    campaign: Campaign | None = None,
    user: User | None = None,
    **kwargs: Any,
) -> AdapterResult[T]:
    """``execute`` plus a budget check and exactly one ``ProviderCall`` row.

    ``job``, ``campaign`` and ``user`` are keyword-only and reserved: an operation argument with
    one of those names must be passed positionally. Raises what ``execute`` raises, plus
    ``BudgetExceeded`` (no provider request is made, one ``error`` row with code
    ``budget_exceeded`` is written). A row is written for failures too. While the operation runs,
    ``attach_raw_payload`` hands its raw answer to this wrapper, which keeps it only when the
    connection's ``CachePolicy`` allows (``Provenance.raw_ref`` then points at it).
    """
    if adapter.key != connection.provider or _value(adapter.category) != _value(
        connection.category
    ):
        raise ValueError("tracked_execute: the adapter does not belong to the connection.")
    name = getattr(operation, "__name__", "") or "unknown"
    context: dict[str, Any] = {"job": job, "campaign": campaign, "user": user}
    try:
        enforce_budget(connection)
    except BudgetExceeded as exc:
        record_call(
            connection, operation=name, status=CallStatus.ERROR, error_code=exc.code, **context
        )
        raise
    policy = effective_policy(connection)
    started = time.perf_counter()
    with collecting_raw(policy.allow_store_raw) as raw:
        try:
            result = execute(adapter, operation, *args, **kwargs)
        except ProviderError as exc:
            record_call(
                connection,
                operation=name,
                status=CallStatus.ERROR,
                error_code=exc.code,
                latency_ms=round((time.perf_counter() - started) * 1000),
                **context,
            )
            raise
    call = record_call(
        connection,
        operation=name,
        status=CallStatus.OK,
        usage=result.usage,
        provenance=result.provenance,
        **context,
    )
    if call is not None and raw.attached:
        ref = store_raw_payload(call, raw.payload, policy)
        if ref:
            provenance = dataclasses.replace(result.provenance, raw_ref=ref)
            result = dataclasses.replace(result, provenance=provenance)
    return result


def budget_summary(connection: IntegrationConnection) -> dict[str, Any]:
    """The budget (or empty defaults) plus this month's standing, as a plain dict for the API."""
    status = check_budget(connection)
    budget = UsageBudget.objects.filter(connection=connection).first()
    credits, cost = month_usage(connection, budget.currency if budget else "")
    return {
        "connection": connection.pk,
        "configured": budget is not None,
        "credit_limit": budget.credit_limit if budget else None,
        "cost_limit": budget.cost_limit if budget else None,
        "currency": budget.currency if budget else "",
        "warn_at_percent": budget.warn_at_percent if budget else 80,
        "hard_stop": budget.hard_stop if budget else True,
        "period_start": status.period_start,
        "period_end": status.period_end,
        "used_credits": credits,
        "used_cost": cost,
        "state": status.action,
        "updated_at": budget.updated_at if budget else None,
    }
