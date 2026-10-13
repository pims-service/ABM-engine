"""Shared state for the rate limiter and circuit breaker, kept in Django's cache (issue #78).

State is a small JSON-like dict per key in a cache every process shares: the database cache
``q_stats`` (Postgres in dev and prod). No Redis, no new table, no migration (ADR 0014).

Updates are read-modify-write, so they run under a lock that is shared by all processes:

* PostgreSQL with a database cache: a transaction-scoped advisory lock
  (``pg_advisory_xact_lock``) on a hash of the key, taken in ``transaction.atomic``; the cache
  write commits together with the lock release.
* Any other database (SQLite in tests): ``transaction.atomic`` plus a process-wide thread lock.
* Non-database caches (``locmem`` in tests): a process-wide thread lock.

Consequences, on purpose: the lock is held for a few milliseconds per call; call these from
outside a long transaction (inside one the advisory lock is held until it ends and a rollback
also rolls back the state); state is best-effort metadata, so an expired or culled entry just
resets a bucket or closes a circuit. The cache entries have a timeout so idle keys disappear.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.core.cache import BaseCache, caches
from django.core.cache.backends.db import DatabaseCache
from django.db import connections, router, transaction

from . import config

KEY_PREFIX = "abm:resilience:"
_process_lock = threading.RLock()


def _advisory_key(name: str) -> int:
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


class SharedState:
    """Read, write and atomically update small dicts in the shared cache."""

    def __init__(self, cache: BaseCache | None = None) -> None:
        self._cache = cache

    @property
    def cache(self) -> BaseCache:
        return self._cache or caches[str(config.get("cache_alias"))]

    def get(self, name: str) -> dict[str, Any] | None:
        value = self.cache.get(KEY_PREFIX + name)
        return value if isinstance(value, dict) else None

    def delete(self, name: str) -> None:
        self.cache.delete(KEY_PREFIX + name)

    @contextmanager
    def update(self, name: str, timeout: float) -> Iterator[dict[str, Any]]:
        """Lock ``name``, yield its current state (a fresh ``{}`` when absent or expired) and
        store whatever the block leaves in it, with ``timeout`` seconds to live. The block must
        mutate the dict in place. An exception leaves the stored state untouched."""
        cache = self.cache
        with self._lock(cache, name):
            state = cache.get(KEY_PREFIX + name)
            if not isinstance(state, dict):
                state = {}
            yield state
            cache.set(KEY_PREFIX + name, state, timeout=max(1, int(timeout)))

    @contextmanager
    def _lock(self, cache: BaseCache, name: str) -> Iterator[None]:
        with _process_lock:
            if not isinstance(cache, DatabaseCache):
                yield
                return
            alias = router.db_for_write(cache.cache_model_class)
            with transaction.atomic(using=alias):
                connection = connections[alias]
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_key(name)])
                yield
