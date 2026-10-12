"""Payload helpers for the cache and raw payload storage (issue #80).

* ``redact_keys`` / ``redact_payload``: mask credential-like keys (and, for raw payloads, secrets
  embedded in text) before anything is stored;
* ``encode_result`` / ``decode_result``: a JSON form of an ``AdapterResult`` restricted to the
  framework's normalized types (never pickle: a cache row must not be able to run code);
* ``cache_key``: the canonical hash of (client, connection, provider, operation, request);
* ``attach_raw_payload``: how an adapter hands the raw provider answer to the wrapper that is
  running it (a no-op when nobody collects, or when the policy forbids keeping it).
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from apps.core.logging import REDACTED, is_sensitive_key, redact_text

from .framework import AdapterResult, IntegrationCategory, Provenance, Usage, normalized

MAX_DEPTH = 12
RAW_REF_PREFIX = "rawpayload:"

#: Dataclasses a cache row may contain: every dataclass of ``framework.normalized``.
_ALLOWED: dict[str, type] = {
    name: obj
    for name, obj in vars(normalized).items()
    if isinstance(obj, type)
    and dataclasses.is_dataclass(obj)
    and obj.__module__ == normalized.__name__
}


class UnsupportedPayload(ValueError):
    """The value cannot be encoded (unknown type, cycle, NaN, too deep). It is simply not cached."""


# ------------------------------------------------------------------ redaction


def redact_keys(value: Any, *, _depth: int = 0) -> Any:
    """Mask the values of credential-like keys, recursively. Other values are kept as they are."""
    if _depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            k: REDACTED if is_sensitive_key(k) else redact_keys(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_keys(v, _depth=_depth + 1) for v in value]
    return value


def _jsonable(value: Any, depth: int = 0) -> Any:
    """A JSON-safe copy: str keys, no NUL characters (PostgreSQL jsonb refuses them), no NaN."""
    if depth > MAX_DEPTH:
        raise UnsupportedPayload("nested too deeply")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedPayload("not a finite number")
        return value
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k).replace("\x00", ""): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v, depth + 1) for v in value]
    raise UnsupportedPayload(f"unsupported type {type(value).__name__}")


def _scrub_text(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _scrub_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_text(v) for v in value]
    return value


def redact_payload(payload: Any) -> Any:
    """A raw provider answer made safe to store: JSON-safe, credential-like keys masked and
    secrets embedded in strings (auth headers, ``key=value`` pairs, URL credentials) scrubbed."""
    return _scrub_text(redact_keys(_jsonable(payload)))


def size_of(payload: Any) -> int:
    """Stored size in bytes (compact UTF-8 JSON)."""
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


# ------------------------------------------------------------------ canonical request


def canonical(value: Any, depth: int = 0) -> Any:
    """A deterministic, JSON-safe form of request parameters (dataclasses, enums, sets, ...).

    Raises ``UnsupportedPayload`` for anything it cannot represent faithfully, so such a call is
    not cached (a lossy key could serve a wrong answer).
    """
    if depth > MAX_DEPTH:
        raise UnsupportedPayload("nested too deeply")
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedPayload("not a finite number")
        return {"$f": repr(value)}
    if isinstance(value, Decimal):
        return {"$d": str(value.normalize())}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, enum.Enum):
        return {"$e": type(value).__name__, "v": canonical(value.value, depth + 1)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "$c": type(value).__name__,
            "f": {
                f.name: canonical(getattr(value, f.name), depth + 1)
                for f in dataclasses.fields(value)
            },
        }
    if isinstance(value, Mapping):
        items = sorted((str(k), canonical(v, depth + 1)) for k, v in value.items())
        return {"$m": items}
    if isinstance(value, list | tuple):
        return [canonical(v, depth + 1) for v in value]
    if isinstance(value, set | frozenset):
        return {"$s": sorted(json.dumps(canonical(v, depth + 1), sort_keys=True) for v in value)}
    raise UnsupportedPayload(f"unsupported type {type(value).__name__}")


def cache_key(
    *,
    client_id: Any,
    connection_id: Any,
    provider: str,
    operation: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> str:
    """The hex SHA-256 of the canonical (client, connection, provider, operation, request)."""
    document = {
        "client": str(client_id),
        "connection": str(connection_id),
        "provider": provider,
        "operation": operation,
        "args": canonical(list(args)),
        "kwargs": canonical(dict(kwargs)),
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ result codec


def _encode(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise UnsupportedPayload("nested too deeply")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedPayload("not a finite number")
        return {"$f": repr(value)}
    if isinstance(value, Decimal):
        return {"$d": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, tuple | list):
        return {"$t": [_encode(v, depth + 1) for v in value]}
    if isinstance(value, Mapping):
        return {"$m": {str(k).replace("\x00", ""): _encode(v, depth + 1) for k, v in value.items()}}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if _ALLOWED.get(name) is not type(value):
            raise UnsupportedPayload(f"{name} cannot be cached")
        return {
            "$c": name,
            "f": {
                f.name: _encode(getattr(value, f.name), depth + 1)
                for f in dataclasses.fields(value)
                if f.init
            },
        }
    raise UnsupportedPayload(f"unsupported type {type(value).__name__}")


def _decode(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise UnsupportedPayload("nested too deeply")
    if not isinstance(value, dict):
        return value
    if "$d" in value:
        return Decimal(value["$d"])
    if "$f" in value:
        return float(value["$f"])
    if "$dt" in value:
        return datetime.fromisoformat(value["$dt"])
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    if "$t" in value:
        return tuple(_decode(v, depth + 1) for v in value["$t"])
    if "$m" in value:
        return {k: _decode(v, depth + 1) for k, v in value["$m"].items()}
    if "$c" in value:
        cls = _ALLOWED.get(value["$c"])
        if cls is None:
            raise UnsupportedPayload("unknown type in cache")
        return cls(**{k: _decode(v, depth + 1) for k, v in value["f"].items()})
    raise UnsupportedPayload("unknown cache marker")


def encode_result(result: AdapterResult[Any]) -> dict[str, Any]:
    """The storable form of ``result``: data, provenance (without ``cache_hit``), warnings and
    quality. Credential-like keys inside ``data`` are masked. Never includes ``usage`` (a cache
    hit costs nothing). Raises ``UnsupportedPayload`` if the data has a type outside the
    normalized vocabulary."""
    provenance = result.provenance.as_dict()
    provenance.pop("cache_hit", None)
    return {
        "v": 1,
        "data": redact_keys(_encode(result.data)),
        "provenance": provenance,
        "warnings": list(result.warnings),
        "quality": result.quality,
    }


def decode_result(document: Any) -> AdapterResult[Any]:
    """Rebuild an ``AdapterResult`` from ``encode_result`` output, flagged ``cache_hit=True`` with
    zero usage. Raises ``UnsupportedPayload`` (or ``ValueError``) for a document that is not one;
    callers treat that as a cache miss."""
    if not isinstance(document, dict) or document.get("v") != 1:
        raise UnsupportedPayload("not a cached result")
    p = document["provenance"]
    provenance = Provenance(
        provider=p["provider"],
        category=IntegrationCategory(p["category"]),
        retrieved_at=datetime.fromisoformat(p["retrieved_at"]),
        terms=p["terms"],
        source_url=p.get("source_url"),
        source_ref=p.get("source_ref"),
        evidence_date=date.fromisoformat(p["evidence_date"]) if p.get("evidence_date") else None,
        request_id=p.get("request_id"),
        cache_hit=True,
        raw_ref=p.get("raw_ref"),
    )
    return AdapterResult(
        data=_decode(document["data"]),
        provenance=provenance,
        usage=Usage(credits=Decimal(0), cost=Decimal(0)),
        warnings=tuple(document.get("warnings") or ()),
        quality=document.get("quality"),
    )


# ------------------------------------------------------------------ raw payload hand-off


@dataclass
class RawCollector:
    """Receives the raw answer an adapter attaches while one tracked call runs."""

    allowed: bool
    payload: Any = None
    attached: bool = False
    dropped_reason: str = field(default="")


_collector: ContextVar[RawCollector | None] = ContextVar("integration_raw_collector", default=None)


@contextmanager
def collecting_raw(allowed: bool) -> Iterator[RawCollector]:
    collector = RawCollector(allowed=allowed)
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        _collector.reset(token)


def attach_raw_payload(payload: Any) -> bool:
    """Hand the raw provider answer (JSON-like: dict, list or str) to the running tracked call.

    Adapters call this from inside an operation, at most once per call. Returns ``True`` if it
    was accepted for storage; ``False`` (the payload is dropped, never persisted) if nothing is
    collecting (a call made outside ``tracked_execute``) or the connection's ``CachePolicy``
    does not allow keeping raw payloads. Cheap to call unconditionally.
    """
    collector = _collector.get()
    if collector is None or not collector.allowed:
        return False
    collector.payload = payload
    collector.attached = True
    return True
