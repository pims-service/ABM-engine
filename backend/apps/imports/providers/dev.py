"""Providers that exist only in development and tests (issue #67).

``register_dev_providers`` puts the ``FakeCompanyProvider`` (name ``fake``) in the registry so the
import flow and the dev UI work end to end without a vendor. It is called from
``ImportsConfig.ready`` only when ``settings.PROVIDER_FAKE_ENABLED`` is true (default on in dev
and test settings, off everywhere else), so a production process never lists it. Real providers
arrive in M3 and register through the same registry.
"""

from __future__ import annotations

from .fake import FakeCompanyProvider
from .registry import ProviderRegistry, default_registry
from .types import CompanySearchRequest, CompanySearchResult

FAKE_PROVIDER_NAME = "fake"
_KEEP_CALLS = 100


class DevFakeProvider(FakeCompanyProvider):
    """The fake provider for a long-running dev process: keeps only the latest calls, so the
    ``calls`` log (useful in tests) cannot grow without bound."""

    def search(self, request: CompanySearchRequest) -> CompanySearchResult:
        try:
            return super().search(request)
        finally:
            del self.calls[:-_KEEP_CALLS]


def register_dev_providers(
    registry: ProviderRegistry | None = None, *, replace: bool = False
) -> None:
    """Register the fake provider (a fresh instance) unless it is already there."""
    target = registry if registry is not None else default_registry
    if FAKE_PROVIDER_NAME in target and not replace:
        return
    target.register(DevFakeProvider(FAKE_PROVIDER_NAME), replace=replace)
