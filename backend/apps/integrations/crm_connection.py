"""The client's CRM connection as a ready, metered adapter (Brief section 12: push to CRM).

Same rules as ``llm_connection``: this package is the only place that knows a concrete adapter,
every push is logged as one ``ProviderCall`` and checked against the connection's budget, and a
missing or unusable connection is a clear, safe message. "Test connection" works for GoHighLevel.
"""

from __future__ import annotations

import functools
from typing import Any

from apps.integrations.adapters.gohighlevel import GoHighLevelAdapter
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
from apps.integrations.services import (
    ConnectionUnavailableError,
    get_credentials,
    resolve_connection,
)
from apps.integrations.testing import register_tester
from apps.integrations.usage import tracked_execute

NOT_CONFIGURED = (
    "No CRM is connected for this client. Add a GoHighLevel connection under Settings > "
    "Integrations (category CRM, with the location id and a private integration token), then "
    "try again."
)
SETTINGS_INCOMPLETE = "The settings are not complete: add the location id and the token."


class CRMUnavailable(Exception):
    """No usable CRM connection. The message is safe to show."""


def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(GoHighLevelAdapter)
    return registry


def crm_provider(client_id: Any) -> str | None:
    """The provider of the client's active CRM connection, or ``None``."""
    connection = resolve_connection(client_id, "crm")
    return None if connection is None else connection.provider


def get_crm_adapter(client_id: Any, *, campaign: Any = None, user: Any = None) -> Any:
    """The metered adapter of the client's oldest active CRM connection."""
    connection = resolve_connection(client_id, "crm")
    if connection is None:
        raise CRMUnavailable(NOT_CONFIGURED)
    if connection.provider != GoHighLevelAdapter.key:
        raise CRMUnavailable(
            f"The CRM '{connection.provider}' is not supported yet. Use GoHighLevel."
        )
    try:
        credentials = get_credentials(connection, purpose="crm_push")
        spec = ConnectionSpec("crm", connection.provider, connection.config, credentials.reveal())
        adapter = _registry().resolve(spec)
    except (ConnectionUnavailableError, CredentialsNotSetError):
        raise CRMUnavailable(
            "The CRM connection has no token. Add it under Settings > Integrations."
        ) from None
    except (DecryptionError, EncryptionConfigError):
        raise CRMUnavailable("The CRM connection's credentials could not be read.") from None
    except ProviderError as error:
        raise CRMUnavailable(
            "The CRM connection is not set up correctly (check the location id and the token)."
        ) from error
    original = adapter.push_contact

    @functools.wraps(original)
    def metered(contact: Any) -> Any:
        return tracked_execute(connection, adapter, original, contact, campaign=campaign, user=user)

    adapter.push_contact = metered
    return adapter


def _test_gohighlevel(
    connection: IntegrationConnection, credentials: Credentials
) -> tuple[bool, str]:
    try:
        spec = ConnectionSpec("crm", connection.provider, connection.config, credentials.reveal())
        adapter = _registry().resolve(spec)
    except ProviderError:
        return False, SETTINGS_INCOMPLETE
    result = adapter.test_connection()
    if result.ok:
        return True, "HighLevel accepted the token and found the location."
    return False, result.message


def register_testers() -> None:
    register_tester("crm", GoHighLevelAdapter.key, _test_gohighlevel, replace=True)
