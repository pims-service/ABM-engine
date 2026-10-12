"""Response cache, raw payload retention and the purge job (issue #80).

``cached_execute`` is ``tracked_execute`` with a per-client response cache in front of it::

    result = cached_execute(connection, adapter, adapter.enrich_company, ref, job=job)
    result = cached_execute(..., force_refresh=True)   # call again, replace the cache entry

What may be cached or kept is decided by the provider's ``CachePolicy`` (``cache_policy.py``);
the default is: nothing. A hit makes no provider request, costs nothing, writes one
``ProviderCall`` row with ``cache_hit=True`` and returns the result with
``provenance.cache_hit=True`` and the ORIGINAL ``retrieved_at``. A refresh makes a new call (new
``ProviderCall``, new ``RawPayload``); the old ones stay, only the cache entry is replaced.

A cached answer is only ever served to the client and connection that caused it: both are part of
the key and of the lookup. ``purge_expired`` (command ``purge_provider_cache``) removes expired
and no-longer-allowed rows.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.core.models import Job

from .cache_policy import CachePolicy, effective_policy
from .framework import Adapter, AdapterResult
from .models import CachedResponse, CallStatus, IntegrationConnection, RawPayload
from .payloads import (
    RAW_REF_PREFIX,
    UnsupportedPayload,
    cache_key,
    decode_result,
    encode_result,
    size_of,
)
from .usage import _count_failure, record_call, tracked_execute

logger = logging.getLogger(__name__)

#: Operations that change something elsewhere or are not repeatable reads: never cached.
NEVER_CACHED = frozenset(
    {
        "push_contact",
        "push_company",
        "export_prospects",
        "trigger",
        "run_status",
        "put_object",
        "get_object",
        "delete_object",
    }
)


def _lookup(connection: IntegrationConnection, key: str) -> AdapterResult[Any] | None:
    row = (
        CachedResponse.objects.for_client(connection.client_id)
        .live()
        .filter(connection=connection, cache_key=key)
        .first()
    )
    if row is None:
        return None
    try:
        result = decode_result(row.payload)
    except (UnsupportedPayload, ValueError, KeyError, TypeError, ArithmeticError):
        logger.warning("dropping an unreadable cache entry (provider=%s)", row.provider)
        CachedResponse.objects.filter(pk=row.pk).delete()
        return None
    return _without_dangling_raw_ref(result, connection)


def _without_dangling_raw_ref(
    result: AdapterResult[Any], connection: IntegrationConnection
) -> AdapterResult[Any]:
    """Drop ``raw_ref`` when the raw payload it points at was purged (or is not this client's)."""
    ref = result.provenance.raw_ref
    if not ref:
        return result
    alive = False
    if ref.startswith(RAW_REF_PREFIX):
        raw_id = ref.removeprefix(RAW_REF_PREFIX)
        try:
            alive = RawPayload.objects.for_client(connection.client_id).filter(pk=raw_id).exists()
        except (ValueError, TypeError):
            alive = False
    if alive:
        return result
    return dataclasses.replace(
        result, provenance=dataclasses.replace(result.provenance, raw_ref=None)
    )


def _store(
    connection: IntegrationConnection,
    operation: str,
    key: str,
    result: AdapterResult[Any],
    policy: CachePolicy,
) -> bool:
    """Write or replace the cache entry. Never raises; ``False`` if nothing was stored."""
    try:
        document = encode_result(result)
        size = size_of(document)
        if size > policy.max_payload_bytes:
            logger.info(
                "not cached: %s.%s answer is %d bytes, over the %d byte cap",
                connection.provider,
                operation,
                size,
                policy.max_payload_bytes,
            )
            return False
        now = timezone.now()
        values = {
            "category": connection.category,
            "provider": connection.provider,
            "operation": operation,
            "payload": document,
            "size_bytes": size,
            "created_at": now,
            "expires_at": now + timedelta(seconds=policy.ttl_for(operation)),
        }
        with transaction.atomic():
            try:
                CachedResponse.objects.update_or_create(
                    connection=connection, cache_key=key, defaults=values
                )
            except IntegrityError:  # a concurrent writer inserted the same key first
                CachedResponse.objects.filter(connection=connection, cache_key=key).update(**values)
        return True
    except UnsupportedPayload:
        return False  # data outside the normalized vocabulary (for example an LLM schema type)
    except Exception:
        _count_failure()
        logger.exception("could not store a cache entry (provider=%s)", connection.provider)
        return False


def cached_execute[T](
    connection: IntegrationConnection,
    adapter: Adapter,
    operation: Callable[..., AdapterResult[T]],
    /,
    *args: Any,
    force_refresh: bool = False,
    job: Job | None = None,
    campaign: Campaign | None = None,
    user: User | None = None,
    **kwargs: Any,
) -> AdapterResult[T]:
    """Serve a live cache entry, else ``tracked_execute`` and cache the answer.

    Nothing is read or stored unless the connection's effective ``CachePolicy`` allows caching
    this operation. ``force_refresh=True`` skips the lookup and replaces the entry. Failed calls
    are never cached. Keyword-only ``force_refresh``, ``job``, ``campaign`` and ``user`` are
    reserved names (pass an operation argument of that name positionally).
    """
    name = getattr(operation, "__name__", "") or "unknown"
    policy = effective_policy(connection)
    key: str | None = None
    if name not in NEVER_CACHED and policy.caches(name):
        try:
            key = cache_key(
                client_id=connection.client_id,
                connection_id=connection.pk,
                provider=connection.provider,
                operation=name,
                args=args,
                kwargs=kwargs,
            )
        except UnsupportedPayload:
            key = None  # a request we cannot key faithfully is never cached
    if key is not None and not force_refresh:
        hit = _lookup(connection, key)
        if hit is not None:
            record_call(
                connection,
                operation=name,
                status=CallStatus.OK,
                provenance=hit.provenance,
                cache_hit=True,
                job=job,
                campaign=campaign,
                user=user,
            )
            return hit
    result = tracked_execute(
        connection, adapter, operation, *args, job=job, campaign=campaign, user=user, **kwargs
    )
    if key is not None:
        _store(connection, name, key, result, policy)
    return result


# ------------------------------------------------------------------ retention


@dataclass
class PurgeReport:
    """How many rows a purge removed (or would remove, for a dry run)."""

    expired_cache: int = 0
    expired_raw: int = 0
    forbidden_cache: int = 0
    forbidden_raw: int = 0

    @property
    def total(self) -> int:
        return self.expired_cache + self.expired_raw + self.forbidden_cache + self.forbidden_raw

    def as_dict(self) -> dict[str, int]:
        return {
            "expired_cache": self.expired_cache,
            "expired_raw": self.expired_raw,
            "forbidden_cache": self.forbidden_cache,
            "forbidden_raw": self.forbidden_raw,
            "total": self.total,
        }


def _remove(queryset: Any, *, dry_run: bool) -> int:
    count: int = queryset.count()
    if count and not dry_run:
        queryset.delete()
    return count


def purge_expired(
    *,
    now: datetime | None = None,
    client: Any = None,
    provider: str | None = None,
    everything: bool = False,
    dry_run: bool = False,
) -> PurgeReport:
    """Remove cache entries and raw payloads that must not be kept any more.

    Always: rows past ``expires_at``; rows whose connection's CURRENT effective policy forbids
    them (caching or raw storage switched off, an operation no longer cacheable, or older than a
    shortened TTL or retention); rows of connections that no longer exist cannot occur (PROTECT).
    ``client`` and ``provider`` limit the scope. ``everything=True`` (needs one of them) removes
    every matching row whatever the policy says, for an erasure or provider opt-out request.
    ``ProviderCall`` rows are never touched.
    """
    moment = now or timezone.now()
    report = PurgeReport()
    cache = CachedResponse.objects.all()
    raw = RawPayload.objects.all()
    if client is not None:
        cache, raw = cache.for_client(client), raw.for_client(client)
    if provider:
        cache, raw = cache.filter(provider=provider), raw.filter(call__provider=provider)
    if everything:
        if client is None and not provider:
            raise ValueError("everything=True needs a client or a provider to limit the purge.")
        report.forbidden_cache = _remove(cache, dry_run=dry_run)
        report.forbidden_raw = _remove(raw, dry_run=dry_run)
        return report

    report.expired_cache = _remove(cache.filter(expires_at__lte=moment), dry_run=dry_run)
    report.expired_raw = _remove(raw.filter(expires_at__lte=moment), dry_run=dry_run)

    connection_ids = set(cache.values_list("connection_id", flat=True)) | set(
        raw.values_list("call__connection_id", flat=True)
    )
    for connection in IntegrationConnection.objects.filter(pk__in=connection_ids):
        policy = effective_policy(connection)
        mine = cache.filter(connection=connection)
        if not policy.allow_cache:
            report.forbidden_cache += _remove(mine, dry_run=dry_run)
        else:
            for operation in set(mine.values_list("operation", flat=True)):
                scoped = mine.filter(operation=operation)
                if not policy.caches(operation):
                    report.forbidden_cache += _remove(scoped, dry_run=dry_run)
                else:  # a TTL shortened after the entry was written
                    cutoff = moment - timedelta(seconds=policy.ttl_for(operation))
                    report.forbidden_cache += _remove(
                        scoped.filter(created_at__lte=cutoff), dry_run=dry_run
                    )
        mine_raw = raw.filter(call__connection=connection)
        if not policy.allow_store_raw:
            report.forbidden_raw += _remove(mine_raw, dry_run=dry_run)
        else:
            cutoff = moment - timedelta(days=policy.retention_days)
            report.forbidden_raw += _remove(
                mine_raw.filter(created_at__lte=cutoff), dry_run=dry_run
            )
    return report


def purge_task() -> dict[str, int]:
    """The function to schedule with Django-Q2 (daily): returns the counts for the task result.

    Schedule.objects.update_or_create(
        name="purge-provider-cache",
        defaults={"func": "apps.integrations.caching.purge_task",
                  "schedule_type": Schedule.DAILY},
    )
    """
    report = purge_expired()
    logger.info("provider cache purge finished: %s", report.as_dict())
    return report.as_dict()
