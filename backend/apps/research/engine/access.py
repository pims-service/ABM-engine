"""The client's LLM connection as a ready adapter, or a clear reason why there is none."""

from __future__ import annotations

from typing import Any

from apps.integrations.adapters.anthropic import AnthropicAdapter
from apps.integrations.crypto import DecryptionError, EncryptionConfigError
from apps.integrations.framework.base import ConnectionSpec
from apps.integrations.framework.errors import ProviderError
from apps.integrations.framework.registry import AdapterRegistry
from apps.integrations.services import (
    ConnectionUnavailableError,
    CredentialsNotSetError,
    get_credentials,
    resolve_connection,
)

NOT_CONFIGURED = (
    "No AI provider is connected for this client. Add an Anthropic connection under "
    "Settings > Integrations (category LLM, with a model and an API key), then run the "
    "analysis again."
)


class AnalysisUnavailable(Exception):
    """The analysis cannot run (for example no LLM connection). The message is safe to show."""


def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(AnthropicAdapter)
    return registry


def get_llm_adapter(client_id: Any) -> Any:
    """The adapter of the client's oldest active LLM connection. Raises ``AnalysisUnavailable``."""
    connection = resolve_connection(client_id, "llm")
    if connection is None:
        raise AnalysisUnavailable(NOT_CONFIGURED)
    if connection.provider != AnthropicAdapter.key:
        raise AnalysisUnavailable(
            f"The LLM provider '{connection.provider}' is not supported yet. Use Anthropic."
        )
    try:
        credentials = get_credentials(connection, purpose="company_analysis")
        spec = ConnectionSpec("llm", connection.provider, connection.config, credentials.reveal())
        return _registry().resolve(spec)
    except (ConnectionUnavailableError, CredentialsNotSetError):
        raise AnalysisUnavailable(
            "The AI connection has no API key. Add it under Settings > Integrations."
        ) from None
    except (DecryptionError, EncryptionConfigError):
        raise AnalysisUnavailable("The AI connection's credentials could not be read.") from None
    except ProviderError as error:
        raise AnalysisUnavailable(
            "The AI connection is not set up correctly (check the model and the API key)."
        ) from error
