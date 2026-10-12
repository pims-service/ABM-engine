"""Job base for provider calls (issues #86 and #78, ADR 0014), built on ``apps.core.jobs``.

``provider_task`` is ``tracked_job`` for tasks that call an integration adapter. It reuses that
machinery (Job and JobItem rows, row-locked counts, retry with backoff through the Django-Q2
scheduler, correlation ids) and adds what provider calls need:

    @provider_task(job_type="enrich_companies", category="company_data", soft_time_limit=200)
    def enrich_companies(ctx: ProviderJobContext, company_ids: list[str]) -> dict[str, Any]:
        def enrich(item: JobItem) -> dict[str, Any]:
            result = ctx.call(ctx.adapter.enrich_company, CompanyRef(...))   # resilient_execute
            return {"fields": len(result.data.fields)}
        ctx.process_items([("company", cid) for cid in company_ids], enrich)
        return {"processed": ctx.job.processed_count}

    job = enqueue(enrich_companies, client=client, created_by=user, company_ids=ids)

What the base does, so task bodies stay small:

* **Fails closed.** Resolves the client's usable ``IntegrationConnection``, reads credentials
  through ``services.get_credentials`` and builds the adapter from the registry. No connection,
  archived, no credentials, undecryptable, unknown provider or invalid settings: the job ends
  ``failed`` at once with a fixed, safe reason (no retries: they cannot get better by waiting).
* **Calls through** ``resilient_execute`` (``ctx.call``): retries, per ``(client, adapter)`` rate
  limit and circuit breaker shared across workers.
* **Errors by type** (``ctx.process_items``), never by message:
  retryable (rate limited, unavailable, timeout, circuit open) -> the job is re-queued after the
  provider's ``retry_after`` (or the backoff), through ``core.jobs.RetryLater``; no worker waits.
  On the last attempt they fail the item instead, so results are kept. Auth, not configured and
  quota errors hit every item, so the job fails now (``FailJob``). Everything else (bad request,
  not found, conflict, upstream error, invalid output, a bug) fails only that item; the job ends
  ``partial`` (or ``failed`` when nothing succeeded).
* **Idempotent on redelivery.** Items already succeeded or failed are skipped; missing items are
  added, never duplicated.
* **Time limits.** ``soft_time_limit`` (seconds, checked between items, after at least one) ends
  the run cleanly and re-queues the rest without using an attempt. The hard limit is Django-Q2's
  ``Q_TASK_TIMEOUT``; the per-call deadline is ``TimeoutPolicy`` (``resilience.timeouts``).
* **Cancellation** between items: ``request_cancel(job_id)``; remaining items are failed as
  "cancelled" and the job ends from its counts (a ``Job`` has no cancelled status).
* **Health**: ``worker_health()`` (worker heartbeat, queue depth, hung calls).

Logs carry the job id (``apps.core.logging``) plus ids, codes and counts; never credentials,
arguments or provider text.
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django_q.models import OrmQ

from apps.core.health import check_worker
from apps.core.jobs import (
    FailJob,
    JobFunc,
    RetryLater,
    add_items,
    backoff_delay,
    complete_item,
    fail_item,
    start_item,
    tracked_job,
)
from apps.core.models import ITEM_TERMINAL, Job, JobItem

from .crypto import CredentialsNotSetError, DecryptionError, EncryptionConfigError
from .framework import (
    Adapter,
    AdapterRegistry,
    AdapterResult,
    ConnectionSpec,
    ProviderAuthError,
    ProviderError,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    default_registry,
)
from .models import IntegrationConnection
from .resilience import Resilience, RetryPolicy, TimeoutPolicy, process_status, resilient_execute
from .resilience.store import SharedState
from .services import ConnectionUnavailableError, get_credentials, resolve_connection

logger = logging.getLogger(__name__)

#: Errors that hit every item the same way: continuing would only repeat them.
SYSTEMIC_ERRORS: tuple[type[ProviderError], ...] = (
    ProviderAuthError,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
)
CANCEL_TTL_SECONDS = 7 * 24 * 3600
Handler = Callable[[JobItem], Any]


# ------------------------------------------------------------------ cancellation


def _cancel_name(job_id: object) -> str:
    return f"cancel:{job_id}"


def request_cancel(job_id: object) -> None:
    """Ask a running provider job to stop after the item it is on. Safe to call twice."""
    with SharedState().update(_cancel_name(job_id), CANCEL_TTL_SECONDS) as state:
        state["requested"] = True


def cancel_requested(job_id: object) -> bool:
    return bool((SharedState().get(_cancel_name(job_id)) or {}).get("requested"))


# ------------------------------------------------------------------ the context


@dataclass
class ProviderJobContext:
    """What a ``provider_task`` body gets: the job, the adapter and the helpers below."""

    job: Job
    connection: IntegrationConnection
    adapter: Adapter
    resilience: Resilience
    max_attempts: int
    base_delay: float
    soft_time_limit: float | None = None
    clock: Callable[[], float] = time.monotonic
    started: float = field(init=False)
    handled: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.started = self.clock()

    def __repr__(self) -> str:  # ids only
        return f"<ProviderJobContext job={self.job.pk} provider={self.adapter.key}>"

    def call(
        self, operation: Callable[..., AdapterResult[Any]], /, *args: Any, **kwargs: Any
    ) -> AdapterResult[Any]:
        """``resilient_execute`` with this job's retry policy, limiter, breaker and deadline."""
        return resilient_execute(
            self.adapter,
            operation,
            *args,
            policy=self.resilience.policy,
            limiter=self.resilience.limiter,
            breaker=self.resilience.breaker,
            timeouts=self.resilience.timeouts,
            **kwargs,
        )

    # -- items
    def ensure_items(self, subjects: Iterable[tuple[str, uuid.UUID | str]]) -> None:
        """Add the subjects that have no item yet (a redelivered run adds none)."""
        wanted = [(t, uuid.UUID(str(i))) for t, i in subjects]
        have = set(self.job.items.values_list("subject_type", "subject_id"))
        missing = [s for s in dict.fromkeys(wanted) if s not in have]
        if missing:
            add_items(self.job, missing)

    @property
    def cancelled(self) -> bool:
        return cancel_requested(self.job.pk)

    def pending_items(self) -> Iterator[JobItem]:
        """The items still to do, one at a time. Between items: stops for a cancel request
        (remaining items fail as cancelled) and for the soft time limit (``RetryLater``)."""
        for item in list(
            self.job.items.exclude(status__in=ITEM_TERMINAL).order_by("created_at", "pk")
        ):
            if self.cancelled:
                self._cancel_remaining()
                return
            if (
                self.soft_time_limit is not None
                and self.handled > 0
                and self.clock() - self.started >= self.soft_time_limit
            ):
                logger.info(
                    "soft time limit reached; re-queueing the rest", extra={"handled": self.handled}
                )
                raise RetryLater(1.0, "soft time limit reached", free=True)
            self.handled += 1
            yield item

    def _cancel_remaining(self) -> None:
        for item in self.job.items.exclude(status__in=ITEM_TERMINAL):
            fail_item(item, "cancelled")
        logger.info("job cancelled between items")

    def process_items(
        self, subjects: Iterable[tuple[str, uuid.UUID | str]], handler: Handler
    ) -> None:
        """Make sure there is an item per subject, then run ``handler(item)`` on each open one.

        The handler returns a JSON result (stored on the item) and may call ``ctx.call``. See
        the module doc for how its errors are handled."""
        self.ensure_items(subjects)
        for item in self.pending_items():
            start_item(item)
            try:
                result = handler(item)
            except ProviderError as error:
                self._item_error(item, error)
            except Exception as exc:
                logger.exception("provider item failed unexpectedly")
                fail_item(item, f"{type(exc).__name__}: unexpected error")
            else:
                complete_item(item, result)

    def _item_error(self, item: JobItem, error: ProviderError) -> None:
        attempts_left = self.job.attempts < self.max_attempts
        if error.retryable and attempts_left:
            delay = max(
                error.retry_after or 0.0,
                backoff_delay(self.job.attempts, self.base_delay).total_seconds(),
            )
            logger.info(
                "provider asked to wait; re-queueing the job",
                extra={"code": error.code, "retry_after": delay},
            )
            raise RetryLater(delay, f"{error.code}: {error}") from error
        fail_item(item, f"{error.code}: {error}")
        if isinstance(error, SYSTEMIC_ERRORS):
            raise FailJob(f"{error.code}: {error}") from error


# ------------------------------------------------------------------ resolving the adapter


def build_adapter(
    job: Job, category: str, provider: str | None, registry: AdapterRegistry
) -> tuple[IntegrationConnection, Adapter]:
    """The client's connection and its adapter, or ``FailJob`` with a safe reason (fail closed)."""
    connection = resolve_connection(job.client_id, category, provider)
    if connection is None:
        raise FailJob("No active integration connection for this provider.")
    try:
        credentials = get_credentials(connection, purpose=job.type)
        spec = ConnectionSpec(
            category,
            connection.provider,
            connection.config,
            credentials.reveal(),
        )
        return connection, registry.resolve(spec)
    except (ConnectionUnavailableError, CredentialsNotSetError):
        raise FailJob("The integration connection has no usable credentials.") from None
    except (DecryptionError, EncryptionConfigError):
        raise FailJob("The integration credentials could not be read.") from None
    except ProviderError as error:
        raise FailJob(f"{error.code}: {error}") from error


# ------------------------------------------------------------------ the decorator

ProviderFunc = Callable[..., dict[str, Any] | None]


def provider_task(
    *,
    job_type: str,
    category: str,
    provider: str | None = None,
    max_attempts: int = 8,
    base_delay: float = 5.0,
    soft_time_limit: float | None = None,
    retry_policy: RetryPolicy | None = None,
    timeouts: TimeoutPolicy | None = None,
    registry: AdapterRegistry | None = None,
) -> Callable[[ProviderFunc], JobFunc]:
    """Turn ``func(ctx, *args, **kwargs)`` into a tracked provider task (see the module doc).

    ``category`` / ``provider`` pick the client's connection (``provider=None``: the oldest active
    one of the category). ``max_attempts`` is higher than for plain jobs because every deferral
    for a rate limit or open circuit uses one. ``soft_time_limit`` must be below Django-Q2's
    ``Q_TASK_TIMEOUT`` (the hard kill), else decoration raises ``ValueError``.
    """
    hard_limit = settings.Q_CLUSTER.get("timeout")
    if soft_time_limit is not None and hard_limit and soft_time_limit >= hard_limit:
        raise ValueError("soft_time_limit must be below Q_CLUSTER['timeout'] (the hard limit).")

    def decorator(func: ProviderFunc) -> JobFunc:
        @functools.wraps(func)
        def body(job: Job, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            connection, adapter = build_adapter(
                job, category, provider, registry or default_registry
            )
            ctx = ProviderJobContext(
                job=job,
                connection=connection,
                adapter=adapter,
                resilience=Resilience.for_adapter(
                    adapter, job.client_id, policy=retry_policy, timeouts=timeouts
                ),
                max_attempts=max_attempts,
                base_delay=base_delay,
                soft_time_limit=soft_time_limit,
            )
            logger.info(
                "provider job started",
                extra={"provider": adapter.key, "category": category, "attempt": job.attempts},
            )
            try:
                return func(ctx, *args, **kwargs)
            except ProviderError as error:
                # A call made outside process_items: same rules, whole-job scope.
                if error.retryable and job.attempts < max_attempts:
                    raise RetryLater(
                        max(
                            error.retry_after or 0.0,
                            backoff_delay(job.attempts, base_delay).total_seconds(),
                        ),
                        f"{error.code}: {error}",
                    ) from error
                raise FailJob(f"{error.code}: {error}") from error

        return tracked_job(job_type=job_type, max_attempts=max_attempts, base_delay=base_delay)(
            body
        )

    return decorator


# ------------------------------------------------------------------ health hook


def queue_depth() -> int:
    """Tasks waiting in the Django-Q2 queue (the ORM broker table)."""
    return int(OrmQ.objects.count())


def worker_health() -> dict[str, Any]:
    """Worker heartbeat, queue depth and per-process resilience signals, as plain data (for the
    readiness page and ops scripts). Never raises: a failure shows as ``ok: False``."""
    worker = check_worker()
    try:
        depth: int | None = queue_depth()
    except Exception:
        logger.warning("could not read the queue depth", exc_info=True)
        depth = None
    return {
        "worker": worker.as_dict(),
        "queue_depth": depth,
        **process_status(),
    }


__all__ = [
    "ProviderJobContext",
    "build_adapter",
    "cancel_requested",
    "provider_task",
    "queue_depth",
    "request_cancel",
    "worker_health",
]
