"""What may be cached and kept per provider (issue #80, docs/integrations/usage-and-cache.md).

A provider's licence may forbid storing or reusing its data. That is encoded as a
``CachePolicy`` looked up by ``(category, provider key)``:

* the default is :data:`NO_CACHE`: nothing is cached and no raw payload is kept until an adapter
  (or the operator, through ``settings.INTEGRATION_CACHE_POLICIES``) opts in after the licence was
  read (``docs/integrations/adapter-requirements.md`` section 9);
* a connection may only NARROW what the provider policy allows (``connection.config["cache"]``):
  the adapter policy is the licence ceiling, a client setting never widens it;
* the policy is separate from ``AdapterCapabilities`` so the merged framework API is unchanged.

Register next to the adapter::

    register_cache_policy(
        IntegrationCategory.COMPANY_DATA,
        "acme",
        CachePolicy(
            allow_cache=True,
            default_ttl_seconds=14 * DAY,
            operation_ttl_seconds={"list_job_postings": DAY},
            allow_store_raw=True,
            retention_days=30,
            note="ACME terms 4.2 allow storing responses for internal use",
        ),
    )
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any

from django.conf import settings

from .models import IntegrationCategory

logger = logging.getLogger(__name__)

DAY = 24 * 60 * 60
DEFAULT_TTL_SECONDS = DAY
DEFAULT_RETENTION_DAYS = 30
#: Default and hard upper bound for one stored payload (cache entry or raw payload).
DEFAULT_MAX_PAYLOAD_BYTES = 256 * 1024
HARD_MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_TTL_SECONDS = 365 * DAY
MAX_RETENTION_DAYS = 365
CONFIG_KEY = "cache"
SETTINGS_NAME = "INTEGRATION_CACHE_POLICIES"


class CachePolicyError(ValueError):
    """An invalid policy value (a bug in the adapter registration or in settings)."""


def _int_in(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CachePolicyError(f"{name} must be a whole number from {low} to {high}.")
    return value


@dataclass(frozen=True)
class CachePolicy:
    """Per provider: may we cache answers, may we keep the raw payload, and for how long.

    ``cacheable_operations=None`` means every operation whose result can be encoded; a set limits
    caching to those operation names. Writes (``push_*``, ``export_*``, ``trigger``, ``put_*``,
    ``delete_*``) are never cached whatever the policy says (see ``cache.NEVER_CACHED``).
    """

    allow_cache: bool = False
    allow_store_raw: bool = False
    default_ttl_seconds: int = DEFAULT_TTL_SECONDS
    operation_ttl_seconds: Mapping[str, int] = field(default_factory=dict)
    cacheable_operations: frozenset[str] | None = None
    retention_days: int = DEFAULT_RETENTION_DAYS
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    note: str = ""  # where the licence was reviewed; documentation only

    def __post_init__(self) -> None:
        if not isinstance(self.allow_cache, bool) or not isinstance(self.allow_store_raw, bool):
            raise CachePolicyError("allow_cache and allow_store_raw must be booleans.")
        _int_in("default_ttl_seconds", self.default_ttl_seconds, 1, MAX_TTL_SECONDS)
        _int_in("retention_days", self.retention_days, 1, MAX_RETENTION_DAYS)
        _int_in("max_payload_bytes", self.max_payload_bytes, 1, HARD_MAX_PAYLOAD_BYTES)
        ttls = dict(self.operation_ttl_seconds)
        for operation, ttl in ttls.items():
            if not isinstance(operation, str) or not operation:
                raise CachePolicyError("operation_ttl_seconds keys must be operation names.")
            _int_in(f"operation_ttl_seconds[{operation}]", ttl, 1, MAX_TTL_SECONDS)
        object.__setattr__(self, "operation_ttl_seconds", ttls)
        if self.cacheable_operations is not None:
            object.__setattr__(self, "cacheable_operations", frozenset(self.cacheable_operations))

    def ttl_for(self, operation: str) -> int:
        return int(self.operation_ttl_seconds.get(operation, self.default_ttl_seconds))

    def caches(self, operation: str) -> bool:
        """True if an answer of ``operation`` may be stored and served again."""
        if not self.allow_cache:
            return False
        return self.cacheable_operations is None or operation in self.cacheable_operations

    def narrowed_by(self, override: object) -> CachePolicy:
        """This policy restricted by a connection's ``config["cache"]``.

        Accepted keys: ``allow_cache`` and ``allow_store_raw`` (only ``false`` has an effect),
        ``ttl_seconds`` (caps every TTL), ``retention_days`` and ``max_payload_bytes`` (cap them).
        Anything malformed fails closed: no caching and no raw retention.
        """
        if override is None:
            return self
        closed = replace(self, allow_cache=False, allow_store_raw=False)
        if not isinstance(override, Mapping):
            return closed
        try:
            out = self
            if override.get("allow_cache", True) is False:
                out = replace(out, allow_cache=False)
            elif not isinstance(override.get("allow_cache", True), bool):
                return closed
            if override.get("allow_store_raw", True) is False:
                out = replace(out, allow_store_raw=False)
            elif not isinstance(override.get("allow_store_raw", True), bool):
                return closed
            if "ttl_seconds" in override:
                cap = _int_in("ttl_seconds", override["ttl_seconds"], 1, MAX_TTL_SECONDS)
                out = replace(
                    out,
                    default_ttl_seconds=min(out.default_ttl_seconds, cap),
                    operation_ttl_seconds={
                        op: min(ttl, cap) for op, ttl in out.operation_ttl_seconds.items()
                    },
                )
            if "retention_days" in override:
                days = _int_in("retention_days", override["retention_days"], 1, MAX_RETENTION_DAYS)
                out = replace(out, retention_days=min(out.retention_days, days))
            if "max_payload_bytes" in override:
                size = _int_in(
                    "max_payload_bytes", override["max_payload_bytes"], 1, HARD_MAX_PAYLOAD_BYTES
                )
                out = replace(out, max_payload_bytes=min(out.max_payload_bytes, size))
            return out
        except CachePolicyError:
            return closed


#: Nothing cached, no raw payload: what every provider gets until it opts in.
NO_CACHE = CachePolicy()

_registry: dict[tuple[str, str], CachePolicy] = {}


def _key(category: object, provider: str) -> tuple[str, str]:
    return str(IntegrationCategory(str(category)).value), provider


def register_cache_policy(
    category: IntegrationCategory | str, provider: str, policy: CachePolicy
) -> None:
    """Declare the policy of one provider (call it where the adapter is registered)."""
    if not isinstance(policy, CachePolicy):
        raise CachePolicyError("policy must be a CachePolicy.")
    _registry[_key(category, provider)] = policy


def unregister_cache_policy(category: IntegrationCategory | str, provider: str) -> None:
    _registry.pop(_key(category, provider), None)


def _from_settings(category: str, provider: str) -> CachePolicy | None:
    configured: Any = getattr(settings, SETTINGS_NAME, None)
    if not isinstance(configured, Mapping):
        return None
    raw = configured.get(f"{category}/{provider}")
    if raw is None:
        return None
    try:
        if not isinstance(raw, Mapping):
            raise CachePolicyError("entry must be an object")
        allowed = {f.name for f in fields(CachePolicy)}
        unknown = set(raw) - allowed
        if unknown:
            raise CachePolicyError(f"unknown keys {sorted(unknown)}")
        return CachePolicy(**raw)
    except (CachePolicyError, TypeError) as exc:
        logger.error(
            "invalid %s entry for %s/%s (%s); caching stays off",
            SETTINGS_NAME,
            category,
            provider,
            exc,
        )
        return NO_CACHE


def get_cache_policy(category: IntegrationCategory | str, provider: str) -> CachePolicy:
    """The provider's policy: ``settings.INTEGRATION_CACHE_POLICIES["<category>/<provider>"]``
    (operator decision) over a code registration, else :data:`NO_CACHE`."""
    category_value, provider_key = _key(category, provider)
    from_settings = _from_settings(category_value, provider_key)
    if from_settings is not None:
        return from_settings
    return _registry.get((category_value, provider_key), NO_CACHE)


def effective_policy(connection: Any) -> CachePolicy:
    """The policy for one connection: the provider's, narrowed by ``config["cache"]``."""
    config = connection.config if isinstance(connection.config, Mapping) else {}
    return get_cache_policy(connection.category, connection.provider).narrowed_by(
        config.get(CONFIG_KEY)
    )
