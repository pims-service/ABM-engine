"""Bridge from a ``CompanyDataAdapter`` to the M2 import contract (#66, #67).

``as_company_search_provider(adapter)`` returns an object satisfying ``CompanySearchProvider``
(``name``, ``capabilities``, ``search``), so the provider import flow keeps using
``execute_search`` while the adapter lives in the new framework. Import this module explicitly;
the package ``__init__`` does not (it would create an import cycle with ``apps.imports``).
"""

from __future__ import annotations

from apps.imports.providers.base import FilterSupport, ProviderCapabilities
from apps.imports.providers.types import CompanySearchRequest, CompanySearchResult

from .categories import SEARCH
from .errors import ProviderBadRequest, ProviderUpstreamError
from .operations import CompanyDataAdapter, execute


class CompanySearchBridge:
    """Adapts a ``CompanyDataAdapter`` to ``CompanySearchProvider``."""

    def __init__(self, adapter: CompanyDataAdapter) -> None:
        if not adapter.capabilities.supports(SEARCH):
            raise ProviderBadRequest("search", provider_name=adapter.key)
        self.adapter = adapter
        self.name = adapter.key
        self.capabilities = ProviderCapabilities(
            terms=adapter.terms,
            supports_search=True,
            # Filters are not negotiated per adapter yet: callers re-check on our side (ADR 0011).
            supports_filters=FilterSupport(),
            max_page_size=adapter.capabilities.max_page_size,
            requires_credentials=adapter.capabilities.requires_credentials,
        )

    def search(self, request: CompanySearchRequest) -> CompanySearchResult:
        result = execute(self.adapter, self.adapter.search, request)
        page = result.data
        if page is None:
            raise ProviderUpstreamError(provider_name=self.name)
        credits = result.usage.credits
        return CompanySearchResult(
            items=tuple(c.to_provider_company() for c in page.items),
            provider_name=self.name,
            retrieved_at=result.provenance.retrieved_at,
            total_estimate=page.total_estimate,
            next_cursor=page.next_cursor,
            raw_ref=result.provenance.raw_ref or "",
            credits_used=float(credits) if credits is not None else None,
            warnings=result.warnings,
        )


def as_company_search_provider(adapter: CompanyDataAdapter) -> CompanySearchBridge:
    return CompanySearchBridge(adapter)
