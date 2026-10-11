"""In-process registry of company search providers (issue #66).

Registration is explicit (``registry.register(provider)``); importing this package registers
nothing. M3 (#74) decides where the real adapters get registered (settings or app ready) and may
wrap or replace this registry; the contract it must keep is ``register`` / ``get`` / ``list`` and
the registration rules below.

Registration refuses (``ProviderRegistryError``, stable ``code``):

* ``invalid_provider``       not a ``CompanySearchProvider`` or no ``ProviderCapabilities``;
* ``invalid_name``           name not matching ``[a-z0-9][a-z0-9_.-]{0,63}``;
* ``terms_missing``          no ``capabilities.terms``;
* ``terms_forbidden``        scraping-style access (``scraping``, ``scraped``, ``crawler`` ...),
                             see the compliance rule in ``base.py``;
* ``terms_unknown``          anything but ``licensed_api`` / ``public_data`` / ``manual``;
* ``duplicate_name``         already registered (pass ``replace=True`` to swap on purpose).
"""

from __future__ import annotations

from .base import ALLOWED_TERMS, CompanySearchProvider, ProviderCapabilities
from .errors import ProviderRegistryError
from .keys import PROVIDER_NAME_PATTERN

_FORBIDDEN_MARKERS = ("scrap", "crawl", "spider", "harvest", "unauthorized", "unlicensed")


def check_terms(terms: object) -> None:
    """Raise ``ProviderRegistryError`` unless ``terms`` is an allowed access basis."""
    if not isinstance(terms, str) or not terms.strip():
        raise ProviderRegistryError("terms_missing", "The provider must declare its terms.")
    value = terms.strip().lower()
    if any(marker in value for marker in _FORBIDDEN_MARKERS):
        raise ProviderRegistryError(
            "terms_forbidden",
            "Scraping-style access is not allowed. "
            "Use a licensed API, public data or manual input.",
        )
    if value not in ALLOWED_TERMS:
        raise ProviderRegistryError(
            "terms_unknown", "The provider terms must be licensed_api, public_data or manual."
        )


class ProviderRegistry:
    """A name -> provider map. Create your own in tests; use the module-level default in the app."""

    def __init__(self) -> None:
        self._providers: dict[str, CompanySearchProvider] = {}

    def register(self, provider: CompanySearchProvider, *, replace: bool = False) -> None:
        name = getattr(provider, "name", None)
        caps = getattr(provider, "capabilities", None)
        if (
            not isinstance(provider, CompanySearchProvider)
            or not isinstance(caps, ProviderCapabilities)
            or not isinstance(name, str)
        ):
            raise ProviderRegistryError(
                "invalid_provider", "A provider needs name, capabilities and search()."
            )
        if not PROVIDER_NAME_PATTERN.fullmatch(name):
            raise ProviderRegistryError(
                "invalid_name", "The provider name must be lower case letters, digits, _ . or -."
            )
        check_terms(caps.terms)
        if name in self._providers and not replace:
            raise ProviderRegistryError("duplicate_name", "A provider with this name exists.")
        self._providers[name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def get(self, name: str) -> CompanySearchProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise ProviderRegistryError("provider_not_found", "Unknown provider.") from None

    def list(self) -> list[CompanySearchProvider]:
        """Registered providers sorted by name."""
        return [self._providers[n] for n in sorted(self._providers)]

    def clear(self) -> None:
        self._providers.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def __len__(self) -> int:
        return len(self._providers)


#: The process-wide default registry. Empty until something registers into it.
default_registry = ProviderRegistry()


def register_provider(provider: CompanySearchProvider, *, replace: bool = False) -> None:
    default_registry.register(provider, replace=replace)


def get_provider(name: str) -> CompanySearchProvider:
    return default_registry.get(name)


def list_providers() -> list[CompanySearchProvider]:
    return default_registry.list()
