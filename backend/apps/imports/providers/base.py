"""The company search provider interface (issue #66), for the M3 adapters to implement.

An adapter is any object with ``name``, ``capabilities`` and ``search(request)``. It translates
our ``CompanySearchRequest`` to the vendor call and the vendor answer to ``ProviderCompany``
items, and translates every vendor failure to a ``ProviderError`` subclass. Callers should use
``execute_search`` rather than ``provider.search`` directly: it enforces the capability limits
and guarantees only typed errors come out.

Compliance (Brief 4 and 17, ADR 0002, ADR 0011): ``capabilities.terms`` is mandatory and must be
``licensed_api`` (an API we hold a licence or key for), ``public_data`` (an open dataset or
public API whose terms allow this use) or ``manual`` (people type or upload data). A provider
that scrapes, or whose access terms are unknown, cannot declare any of these and is refused at
registration. Profile URLs are identifiers: no adapter may fetch them.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .errors import ProviderBadRequest, ProviderError, ProviderUnavailable
from .types import MAX_PAGE_SIZE, CompanySearchRequest, CompanySearchResult

Terms = Literal["licensed_api", "public_data", "manual"]
ALLOWED_TERMS: frozenset[str] = frozenset({"licensed_api", "public_data", "manual"})


@dataclass(frozen=True)
class FilterSupport:
    """Which request filters the provider applies itself. A filter it does not support is
    ignored by the provider; the caller must not rely on it (re-check on our side)."""

    countries: bool = False
    industries: bool = False
    size: bool = False
    business_model: bool = False


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can do and under what terms it is used."""

    terms: str  # one of ALLOWED_TERMS; checked at registration
    supports_search: bool = True
    supports_filters: FilterSupport = FilterSupport()
    max_page_size: int = MAX_PAGE_SIZE
    requires_credentials: bool = True


@runtime_checkable
class CompanySearchProvider(Protocol):
    """Structural interface of a company search adapter."""

    name: str  # lower case, [a-z0-9][a-z0-9_.-]{0,63}, unique in a registry
    capabilities: ProviderCapabilities

    def search(self, request: CompanySearchRequest) -> CompanySearchResult:
        """Return one page for ``request`` (the page at ``request.cursor``).

        Raise only ``ProviderError`` subclasses; never include credentials or upstream bodies
        in anything raised. Return at most ``capabilities.max_page_size`` items."""
        ...


def execute_search(
    provider: CompanySearchProvider, request: CompanySearchRequest
) -> CompanySearchResult:
    """Run one page search with the contract enforced.

    * refuses a provider without search support (``ProviderBadRequest``);
    * caps ``page_size`` at the provider's ``max_page_size``;
    * turns any non-``ProviderError`` exception into ``ProviderUnavailable`` (the original is
      chained as ``__cause__`` but never shown);
    * checks the result: right type, ``provider_name`` matches, no more items than asked.
    """
    caps = provider.capabilities
    if not caps.supports_search:
        raise ProviderBadRequest("search", provider_name=provider.name)
    size = request.effective_page_size(caps.max_page_size)
    if size != request.page_size:
        request = dataclasses.replace(request, page_size=size)
    try:
        result = provider.search(request)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderUnavailable(provider_name=provider.name) from exc
    if (
        not isinstance(result, CompanySearchResult)
        or result.provider_name != provider.name
        or len(result.items) > size
    ):
        raise ProviderUnavailable(provider_name=provider.name)
    return result
