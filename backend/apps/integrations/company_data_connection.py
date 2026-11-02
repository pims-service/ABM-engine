"""The client's company-data connection as a ready, metered adapter (issue #93, Brief section 12).

Same rules as ``people_connection``: this module is the one place that knows the concrete
company-data adapters, every call is logged as one ``ProviderCall`` and checked against the
connection's budget, and a missing or unusable connection is a clear, safe message. "Test
connection" works for each provider.

Supported: Apollo, People Data Labs and Hunter (docs/integrations/company-data-providers.md).
Where no connection exists, the offline fake named by ``settings.RESEARCH_DEV_COMPANY_ADAPTER``
(a dotted path, set in the dev and test settings only; production code never imports the fakes)
is used, and production gets a message saying what to connect.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from apps.integrations.adapters.apollo import ApolloAdapter
from apps.integrations.adapters.hunter import HunterCompanyAdapter
from apps.integrations.adapters.peopledatalabs import PeopleDataLabsAdapter
from apps.integrations.crypto import (
    Credentials,
    CredentialsNotSetError,
    DecryptionError,
    EncryptionConfigError,
)
from apps.integrations.framework.base import ConnectionSpec
from apps.integrations.framework.errors import ProviderError
from apps.integrations.framework.registry import AdapterRegistry
from apps.integrations.models import IntegrationConnection
from apps.integrations.people_connection import metered_operation
from apps.integrations.services import (
    ConnectionUnavailableError,
    get_credentials,
    resolve_connection,
)
from apps.integrations.testing import register_tester

COMPANY_DATA = "company_data"
SUPPORTED = ("apollo", "peopledatalabs", "hunter")
LABEL = {"apollo": "Apollo", "peopledatalabs": "People Data Labs", "hunter": "Hunter"}
#: The operations that are metered, each with the feature that says the adapter has it.
_OPERATIONS = (
    ("search", "search"),
    ("enrich_company", "enrich_company"),
    ("list_job_postings", "job_postings"),
)


class CompanyDataUnavailable(Exception):
    """No usable company-data source. The message is safe to show."""


def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in (ApolloAdapter, PeopleDataLabsAdapter, HunterCompanyAdapter):
        registry.register(adapter)
    return registry


def _message_missing() -> str:
    names = ", ".join(LABEL[key] for key in SUPPORTED)
    return (
        "No company data provider is connected for this client. Add one under Settings > "
        f"Integrations (category Company Data: {names}, with your API key), then try again."
    )


def _build_adapter(
    connection: IntegrationConnection, *, job: Any = None, campaign: Any = None, user: Any = None
) -> Any:
    """The metered adapter of ``connection``, or ``CompanyDataUnavailable`` with a safe message."""
    if connection.provider not in SUPPORTED:
        raise CompanyDataUnavailable(
            f"The provider '{connection.provider}' is not supported yet for company data."
        )
    try:
        credentials = get_credentials(connection, purpose="company_data_lookup")
        spec = ConnectionSpec(
            COMPANY_DATA, connection.provider, connection.config, credentials.reveal()
        )
        adapter: Any = _registry().resolve(spec)
    except (ConnectionUnavailableError, CredentialsNotSetError):
        raise CompanyDataUnavailable(
            "The connection has no API key. Add it under Settings > Integrations."
        ) from None
    except (DecryptionError, EncryptionConfigError):
        raise CompanyDataUnavailable("The connection's credentials could not be read.") from None
    except ProviderError as error:
        raise CompanyDataUnavailable(
            "The connection is not set up correctly (check the API key)."
        ) from error
    for name, feature in _OPERATIONS:
        if not adapter.capabilities.supports(feature):
            continue  # an operation this provider does not offer is left as it is
        setattr(
            adapter,
            name,
            metered_operation(
                connection, adapter, getattr(adapter, name), job=job, campaign=campaign, user=user
            ),
        )
    return adapter


def get_adapter(client_id: Any, *, job: Any = None, campaign: Any = None, user: Any = None) -> Any:
    """The metered adapter of the client's oldest active company-data connection, or
    ``CompanyDataUnavailable`` with a message that says what to do."""
    connection = resolve_connection(client_id, COMPANY_DATA)
    if connection is None:
        dev_adapter = getattr(settings, "RESEARCH_DEV_COMPANY_ADAPTER", "")
        if dev_adapter:
            return import_string(dev_adapter)()
        raise CompanyDataUnavailable(_message_missing())
    return _build_adapter(connection, job=job, campaign=campaign, user=user)


# ------------------------------------------------------------------ Find companies (the import)
def searchable_keys() -> tuple[str, ...]:
    """The supported providers whose adapter can search (Apollo and People Data Labs)."""
    return tuple(
        adapter.key
        for adapter in _registry().list(COMPANY_DATA)
        if adapter.capabilities.supports("search")
    )


def connected_search_providers(client_id: Any) -> list[Any]:
    """The search providers the client has an active connection for, described (name and
    capabilities) without reading any credential. Used to list what Find companies can search."""
    described: list[Any] = []
    for key in searchable_keys():
        if resolve_connection(client_id, COMPANY_DATA, key) is None:
            continue
        cls = _registry().get_class(COMPANY_DATA, key)
        described.append(_Described(cls))
    return described


class _Described:
    """What the providers list needs of a provider: a name and its capabilities."""

    def __init__(self, adapter_cls: Any) -> None:
        from apps.imports.providers.base import FilterSupport, ProviderCapabilities

        self.name: str = adapter_cls.key
        self.capabilities = ProviderCapabilities(
            terms=adapter_cls.terms,
            supports_search=True,
            supports_filters=FilterSupport(),
            max_page_size=adapter_cls.capabilities.max_page_size,
            requires_credentials=adapter_cls.capabilities.requires_credentials,
        )


def search_provider(
    client_id: Any, name: str, *, campaign: Any = None, user: Any = None, job: Any = None
) -> Any | None:
    """The client's connection to ``name`` as a company search provider for the import flow, or
    ``None`` when the client has no active connection to a searchable provider of that name.
    ``CompanyDataUnavailable`` when the connection exists but cannot be used (no key, bad
    settings); every call is metered like any other provider call."""
    from apps.integrations.framework.bridge import as_company_search_provider

    if name not in searchable_keys():
        return None
    connection = resolve_connection(client_id, COMPANY_DATA, name)
    if connection is None:
        return None
    adapter = _build_adapter(connection, job=job, campaign=campaign, user=user)
    return as_company_search_provider(adapter)


def _test(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
    try:
        spec = ConnectionSpec(
            COMPANY_DATA, connection.provider, connection.config, credentials.reveal()
        )
        adapter = _registry().resolve(spec)
    except ProviderError:
        return False, "The settings are not complete: add the API key."
    result = adapter.test_connection()
    if result.ok:
        return True, "The provider accepted the API key."
    return False, result.message


def register_testers() -> None:
    for provider in SUPPORTED:
        register_tester(COMPANY_DATA, provider, _test, replace=True)


def active_provider(client_id: Any) -> str | None:
    """The provider key of the client's oldest active company-data connection, or ``None``."""
    connection = resolve_connection(client_id, COMPANY_DATA)
    return connection.provider if connection is not None else None
