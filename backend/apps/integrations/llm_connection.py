"""The client's LLM connection as a ready adapter, or a clear reason why there is none.

Lives in the integrations package because it is the one place allowed to know a concrete adapter
(see tests/test_integration_framework.py). Callers get an ``LLMAdapter`` and never a vendor.

Every call made through the adapter returned here is metered: one ``ProviderCall`` row per call
(tokens, cost, who and which campaign) and the connection's monthly budget is enforced before the
provider is contacted (``BudgetExceeded``). The key of a connection can also be tested from
Settings > Integrations (``register_testers``).
"""

from __future__ import annotations

import functools
from typing import Any

from apps.integrations.adapters.anthropic import AnthropicAdapter
from apps.integrations.adapters.gemini import GeminiAdapter
from apps.integrations.adapters.openai import OpenAIAdapter
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
    "No AI provider is connected for this client. Add an Anthropic, Gemini or OpenAI "
    "connection under Settings > Integrations (category LLM, with a model and an API key), "
    "then run the analysis again."
)
SETTINGS_INCOMPLETE = "The settings are not complete: add a model and an API key."


class LLMUnavailable(Exception):
    """No usable LLM connection (for example none is set up). The message is safe to show."""


LLM_ADAPTERS = (AnthropicAdapter, GeminiAdapter, OpenAIAdapter)
SUPPORTED_PROVIDERS = tuple(adapter.key for adapter in LLM_ADAPTERS)


def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in LLM_ADAPTERS:
        registry.register(adapter)
    return registry


def _meter(
    adapter: Any,
    connection: IntegrationConnection,
    *,
    job: Any,
    campaign: Any,
    user: Any,
) -> Any:
    """Route the adapter's model calls through ``tracked_execute`` (budget check and one
    ``ProviderCall`` row per call, failures included)."""
    original = adapter.generate_structured

    @functools.wraps(original)
    def metered(prompt: Any, schema: Any, **kwargs: Any) -> Any:
        return tracked_execute(
            connection,
            adapter,
            original,
            prompt,
            schema,
            job=job,
            campaign=campaign,
            user=user,
            **kwargs,
        )

    adapter.generate_structured = metered
    return adapter


def get_llm_adapter(
    client_id: Any,
    *,
    job: Any = None,
    campaign: Any = None,
    user: Any = None,
) -> Any:
    """The metered adapter of the client's oldest active LLM connection.

    Raises ``LLMUnavailable`` (safe message) when there is no connection, no key or bad settings.
    ``job``, ``campaign`` and ``user`` are only recorded with each call.
    """
    connection = resolve_connection(client_id, "llm")
    if connection is None:
        raise LLMUnavailable(NOT_CONFIGURED)
    if connection.provider not in SUPPORTED_PROVIDERS:
        raise LLMUnavailable(
            f"The LLM provider '{connection.provider}' is not supported yet. "
            "Use Anthropic, Gemini or OpenAI."
        )
    try:
        credentials = get_credentials(connection, purpose="company_analysis")
        spec = ConnectionSpec("llm", connection.provider, connection.config, credentials.reveal())
        adapter = _registry().resolve(spec)
    except (ConnectionUnavailableError, CredentialsNotSetError):
        raise LLMUnavailable(
            "The AI connection has no API key. Add it under Settings > Integrations."
        ) from None
    except (DecryptionError, EncryptionConfigError):
        raise LLMUnavailable("The AI connection's credentials could not be read.") from None
    except ProviderError as error:
        raise LLMUnavailable(
            "The AI connection is not set up correctly (check the model and the API key)."
        ) from error
    return _meter(adapter, connection, job=job, campaign=campaign, user=user)


def _test_llm(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
    """Settings > Integrations "Test connection": asks the provider to authenticate the key
    (no tokens are generated). Anthropic does not check that the configured model exists;
    Gemini and OpenAI do."""
    try:
        spec = ConnectionSpec("llm", connection.provider, connection.config, credentials.reveal())
        adapter = _registry().resolve(spec)
    except ProviderError:
        return False, SETTINGS_INCOMPLETE
    result = adapter.test_connection()
    if result.ok:
        return True, "The provider accepted the key."
    return False, result.message


def register_testers() -> None:
    """Make "Test connection" work for the providers that have an adapter."""
    for key in SUPPORTED_PROVIDERS:
        register_tester("llm", key, _test_llm, replace=True)
