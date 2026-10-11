"""The standard result envelope: ``AdapterResult[T]`` with provenance and usage (issue #75).

Every adapter operation returns an ``AdapterResult``. The ``data`` is one of the normalized types
(``normalized.py``) or a small per-category result; ``provenance`` says where and when it came
from; ``usage`` says what it cost; ``warnings`` mark a partial answer.

Provenance rules (Brief section 5: store source and date for important evidence):

* ``provider``, ``category``, ``terms`` and a timezone-aware ``retrieved_at`` are always required;
* if ``data`` contains anything evidence-bearing (company, person, signal, email verdict, ...) the
  provenance must also carry a source: ``source_url`` (a real http(s) URL the provider gave) or
  ``source_ref`` (the provider's record or request identifier). Otherwise ``EnvelopeError``;
* ``evidence_date`` is the date the source states for the evidence (``None`` if it does not say);
* ``raw_ref`` is a pointer to the raw payload kept elsewhere (#80), never the payload; it is
  scrubbed of secrets and size capped.

A violation raises ``EnvelopeError`` at construction, so an adapter that forgets provenance fails
its own tests instead of shipping unattributed data. ``execute`` (``operations.py``) turns that
into ``ProviderUpstreamError`` at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from . import _clean
from .categories import ADAPTER_KEY_PATTERN, IntegrationCategory, check_terms
from .errors import EnvelopeError, ProviderRegistryError
from .normalized import QUALITIES, iter_evidence

MAX_WARNINGS = 20
MAX_WARNING_LENGTH = 200
MAX_REF_LENGTH = 200
_set = _clean.set_attr


@dataclass(frozen=True)
class Provenance:
    """Where a result came from."""

    provider: str
    category: IntegrationCategory
    retrieved_at: datetime
    terms: str
    source_url: str | None = None
    source_ref: str | None = None
    evidence_date: date | None = None
    request_id: str | None = None
    cache_hit: bool = False
    raw_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not ADAPTER_KEY_PATTERN.fullmatch(self.provider):
            raise EnvelopeError("provenance.provider must be an adapter key")
        try:
            _set(self, "category", IntegrationCategory(self.category))
        except ValueError:
            raise EnvelopeError("provenance.category is not an integration category") from None
        try:
            _clean.aware(self.retrieved_at, "retrieved_at")
            check_terms(self.terms)
        except (ValueError, ProviderRegistryError) as exc:
            raise EnvelopeError(f"provenance: {exc}") from None
        _set(self, "terms", self.terms.strip().lower())
        if self.source_url is not None:
            url = _clean.url(self.source_url)
            if url is None:
                raise EnvelopeError("provenance.source_url must be an http(s) URL")
            _set(self, "source_url", url)
        _set(self, "source_ref", _clean.scrubbed_text(self.source_ref, MAX_REF_LENGTH))
        if self.evidence_date is not None:
            day = _clean.day(self.evidence_date)
            if day is None:
                raise EnvelopeError("provenance.evidence_date must be a date")
            _set(self, "evidence_date", day)
        _set(self, "request_id", _clean.scrubbed_text(self.request_id, MAX_REF_LENGTH))
        _set(self, "raw_ref", _clean.scrubbed_text(self.raw_ref, MAX_REF_LENGTH))
        _set(self, "cache_hit", self.cache_hit is True)

    @property
    def has_source(self) -> bool:
        return bool(self.source_url or self.source_ref)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form (for ``DataSource`` rows and logs)."""
        return {
            "provider": self.provider,
            "category": self.category.value,
            "retrieved_at": self.retrieved_at.isoformat(),
            "terms": self.terms,
            "source_url": self.source_url,
            "source_ref": self.source_ref,
            "evidence_date": self.evidence_date.isoformat() if self.evidence_date else None,
            "request_id": self.request_id,
            "cache_hit": self.cache_hit,
            "raw_ref": self.raw_ref,
        }


@dataclass(frozen=True)
class Usage:
    """What one call cost. Unknown parts stay ``None``; a negative or non-finite number is
    treated as unknown. ``cost`` is money in ``currency`` (ISO 4217); ``credits`` is the
    provider's own unit."""

    credits: Decimal | None = None
    cost: Decimal | None = None
    currency: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    latency_ms: int | None = None

    def __post_init__(self) -> None:
        zero = Decimal(0)
        _set(self, "credits", _clean.decimal(self.credits, minimum=zero))
        _set(self, "cost", _clean.decimal(self.cost, minimum=zero))
        cur = _clean.text(self.currency, 3)
        _set(self, "currency", cur.upper() if cur and len(cur) == 3 and cur.isalpha() else None)
        for name in ("input_tokens", "output_tokens"):
            _set(self, name, _clean.integer(getattr(self, name), maximum=100_000_000))
        _set(self, "model", _clean.text(self.model, 100))
        _set(self, "latency_ms", _clean.integer(self.latency_ms, maximum=86_400_000))

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass(frozen=True)
class AdapterResult[T]:
    """The envelope around an adapter's output. ``data`` is ``None`` for "provider has nothing"
    (never a fabricated empty record). ``quality`` is an optional ``high``/``medium``/``low`` flag
    for the provider's own confidence in the whole answer."""

    data: T | None
    provenance: Provenance
    usage: Usage = Usage()
    warnings: tuple[str, ...] = ()
    quality: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance):
            raise EnvelopeError("an AdapterResult needs provenance")
        if not isinstance(self.usage, Usage):
            raise EnvelopeError("usage must be a Usage")
        if isinstance(self.warnings, str):  # type: ignore[unreachable]
            raise EnvelopeError("warnings must be a sequence of short messages")
        warnings = [_clean.scrubbed_text(w, MAX_WARNING_LENGTH) for w in self.warnings]
        _set(self, "warnings", tuple(w for w in warnings if w)[:MAX_WARNINGS])
        _set(self, "quality", self.quality if self.quality in QUALITIES else None)
        if not self.provenance.has_source and any(True for _ in iter_evidence(self.data)):
            raise EnvelopeError(
                "evidence needs provenance with a source (source_url or source_ref)"
            )

    @property
    def has_data(self) -> bool:
        return self.data is not None

    def log_summary(self) -> dict[str, Any]:
        """A small, secret-free dict for structured logs and ``ProviderCall`` rows (#79): the
        provenance, the usage and the warning count, never ``data``."""
        usage = self.usage
        return {
            **self.provenance.as_dict(),
            "credits": str(usage.credits) if usage.credits is not None else None,
            "cost": str(usage.cost) if usage.cost is not None else None,
            "currency": usage.currency,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "model": usage.model,
            "latency_ms": usage.latency_ms,
            "warnings": len(self.warnings),
            "has_data": self.has_data,
        }
