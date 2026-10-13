"""Test helpers for the resilience layer (issue #78): a fake clock and a scripted adapter."""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any, ClassVar

from django.core.cache.backends.locmem import LocMemCache

from apps.integrations.framework import (
    AdapterResult,
    CompanyPage,
    CompanyRef,
    NormalizedCompany,
    ProviderError,
)
from apps.integrations.framework.base import AdapterCapabilities, RateLimit
from apps.integrations.framework.categories import ENRICH_COMPANY, SEARCH
from apps.integrations.framework.fakes import FakeCompanyDataAdapter
from apps.integrations.resilience.store import SharedState

_names = itertools.count()


class FakeClock:
    """A clock that only moves when told to; ``sleep`` advances it (and records the call)."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def local_state() -> SharedState:
    """A shared state on its own in-process cache (no database); share one value between two
    limiters to simulate two processes that see the same cache."""
    return SharedState(LocMemCache(f"resilience-{next(_names)}", {}))


class ScriptedAdapter(FakeCompanyDataAdapter):
    """A fake company adapter whose ``enrich_company`` follows a script: each entry is a
    ``ProviderError`` to raise or ``None`` to succeed. The script is per instance."""

    key = "scripted_company"
    display_name = "Scripted company data"
    capabilities = AdapterCapabilities(
        features=frozenset({SEARCH, ENRICH_COMPANY}),
        requires_credentials=False,
        rate_limit=RateLimit(per_minute=60),
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
        "x-credentials": {"required": [], "optional": ["api_key"]},
    }

    def __init__(self, *args: Any, script: list[ProviderError | None] | None = None, **kw: Any):
        super().__init__(*args, **kw)
        self.script = list(script or [])
        self.attempts = 0

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        self.attempts += 1
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step
        return super().enrich_company(company)

    def search(self, request: Any) -> AdapterResult[CompanyPage]:
        self.attempts += 1
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step
        return super().search(request)
