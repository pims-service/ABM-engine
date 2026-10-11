"""The adapter registry (issue #74, ADR 0013): (category, key) -> adapter class.

Registration is explicit (``registry.register(AdapterClass)``); importing this package registers
nothing, and ``default_registry`` starts empty. Business code asks the registry for an adapter
by category and key (``resolve(ConnectionSpec(...))``) and never imports a vendor module.

Registration refuses (``ProviderRegistryError``, stable ``code``): ``invalid_adapter``,
``invalid_key``, ``invalid_category``, ``category_mismatch`` (not a subclass of its category base),
``invalid_display_name``, ``terms_missing`` / ``terms_forbidden`` / ``terms_unknown``,
``invalid_capabilities``, ``invalid_config_schema``, ``duplicate_adapter`` (pass ``replace=True``).
"""

from __future__ import annotations

import builtins
from typing import Any

from .base import Adapter, ConnectionSpec, check_adapter_class
from .categories import IntegrationCategory
from .errors import ProviderNotConfigured, ProviderNotFound, ProviderRegistryError
from .operations import CATEGORY_BASES


class AdapterRegistry:
    """Create your own in tests; the app uses ``default_registry``."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[IntegrationCategory, str], type[Adapter]] = {}

    def register(self, adapter_cls: type[Adapter], *, replace: bool = False) -> None:
        check_adapter_class(adapter_cls)
        category = IntegrationCategory(adapter_cls.category)
        if not issubclass(adapter_cls, CATEGORY_BASES[category]):
            raise ProviderRegistryError(
                "category_mismatch", "The adapter must extend the base class of its category."
            )
        slot = (category, adapter_cls.key)
        if slot in self._adapters and not replace:
            raise ProviderRegistryError("duplicate_adapter", "An adapter with this key exists.")
        self._adapters[slot] = adapter_cls

    def unregister(self, category: IntegrationCategory | str, key: str) -> None:
        self._adapters.pop((IntegrationCategory(category), key), None)

    def get_class(self, category: IntegrationCategory | str, key: str) -> type[Adapter]:
        """The adapter class, or ``ProviderNotFound`` (code ``provider_not_found``)."""
        try:
            return self._adapters[(IntegrationCategory(category), key)]
        except (KeyError, ValueError):
            raise ProviderNotFound(provider_name=key) from None

    def resolve(self, spec: ConnectionSpec, **kwargs: Any) -> Adapter:
        """Build the adapter for ``spec``: ``ProviderNotFound`` for an unknown key,
        ``ProviderNotConfigured`` (naming fields, never values) for bad settings or credentials."""
        cls = self.get_class(spec.category, spec.key)
        validation = cls.validate_config(spec.config, spec.credentials)
        if not validation.ok:
            raise ProviderNotConfigured(validation.fields, provider_name=spec.key)
        return cls.from_spec(spec, **kwargs)

    def list(
        self, category: IntegrationCategory | str | None = None
    ) -> builtins.list[type[Adapter]]:
        """Registered classes sorted by (category, key), optionally of one category."""
        wanted = IntegrationCategory(category) if category is not None else None
        return [
            self._adapters[slot]
            for slot in sorted(self._adapters)
            if wanted is None or slot[0] == wanted
        ]

    def supporting(
        self, category: IntegrationCategory | str, feature: str
    ) -> builtins.list[type[Adapter]]:
        """Adapters of ``category`` that declare ``feature`` (capability negotiation)."""
        return [c for c in self.list(category) if c.capabilities.supports(feature)]

    def supports(self, category: IntegrationCategory | str, key: str, feature: str) -> bool:
        return self.get_class(category, key).capabilities.supports(feature)

    def describe(
        self, category: IntegrationCategory | str | None = None
    ) -> builtins.list[dict[str, Any]]:
        """JSON-safe metadata of the registered adapters (for the settings UI, #77): no
        credentials, only the schema and credential names."""
        return [
            {
                "category": c.category.value,
                "key": c.key,
                "display_name": c.display_name,
                "version": c.version,
                "terms": c.terms,
                "features": sorted(c.capabilities.features),
                "requires_credentials": c.capabilities.requires_credentials,
                "test_connection_costs_credits": c.capabilities.test_connection_costs_credits,
                "config_schema": dict(c.config_schema),
            }
            for c in self.list(category)
        ]

    def clear(self) -> None:
        self._adapters.clear()

    def __contains__(self, slot: object) -> bool:
        return slot in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)


#: The process-wide registry. Empty until something registers into it.
default_registry = AdapterRegistry()


def register_adapter(adapter_cls: type[Adapter], *, replace: bool = False) -> None:
    default_registry.register(adapter_cls, replace=replace)


def get_adapter(spec: ConnectionSpec, **kwargs: Any) -> Adapter:
    return default_registry.resolve(spec, **kwargs)


def list_adapters(
    category: IntegrationCategory | str | None = None,
) -> builtins.list[type[Adapter]]:
    return default_registry.list(category)
