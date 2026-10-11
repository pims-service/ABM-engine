"""Typed errors for company search providers (issue #66).

The error family now lives in ``apps.integrations.framework.errors`` (issues #74 and #75,
ADR 0013) so every integration category shares it. This module re-exports the #66 names unchanged
(same classes, codes and messages), so ``from apps.imports.providers.errors import ...`` and
``from apps.imports.providers import ...`` keep working.
"""

from __future__ import annotations

from apps.integrations.framework.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderRegistryError,
    ProviderUnavailable,
)

__all__ = [
    "ProviderAuthError",
    "ProviderBadRequest",
    "ProviderError",
    "ProviderQuotaExceeded",
    "ProviderRateLimited",
    "ProviderRegistryError",
    "ProviderUnavailable",
]
