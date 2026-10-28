"""The client's people-data and email connections as ready, metered adapters (Brief section 12).

Same rules as ``crm_connection``: this module is the only place that knows the concrete adapters,
every call is logged as one ``ProviderCall`` and checked against the connection's budget, and a
missing or unusable connection is a clear, safe message. "Test connection" works for both
SignalHire and ZeroBounce.
"""

from __future__ import annotations

import functools
from typing import Any

from apps.integrations.adapters.signalhire import SignalHireEmailAdapter, SignalHirePeopleAdapter
from apps.integrations.adapters.zerobounce import ZeroBounceAdapter
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

PEOPLE = "people_data"
EMAIL = "email_enrichment"
VERIFY = "email_verification"
#: The provider each category supports so far.
PROVIDER = {PEOPLE: "signalhire", EMAIL: "signalhire", VERIFY: "zerobounce"}
LABEL = {"signalhire": "SignalHire", "zerobounce": "ZeroBounce"}
_WHAT = {PEOPLE: "people data", EMAIL: "email enrichment", VERIFY: "email verification"}


class PeopleUnavailable(Exception):
    """No usable connection. The message is safe to show."""


def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(SignalHirePeopleAdapter)
    registry.register(SignalHireEmailAdapter)
    registry.register(ZeroBounceAdapter)
    return registry


def _message_missing(category: str) -> str:
    return (
        f"No {_WHAT[category]} provider is connected for this client. Add a "
        f"{LABEL[PROVIDER[category]]} "
        f"connection under Settings > Integrations (category "
        f"{category.replace('_', ' ').title()}, with your API key), then try again."
    )


def get_adapter(category: str, client_id: Any, *, campaign: Any = None, user: Any = None) -> Any:
    """The metered adapter of the client's oldest active connection of ``category``."""
    connection = resolve_connection(client_id, category)
    if connection is None:
        raise PeopleUnavailable(_message_missing(category))
    if connection.provider != PROVIDER[category]:
        raise PeopleUnavailable(
            f"The provider '{connection.provider}' is not supported yet for {_WHAT[category]}."
        )
    try:
        credentials = get_credentials(connection, purpose=f"{category}_lookup")
        spec = ConnectionSpec(
            category, connection.provider, connection.config, credentials.reveal()
        )
        adapter: Any = _registry().resolve(spec)
    except (ConnectionUnavailableError, CredentialsNotSetError):
        raise PeopleUnavailable(
            "The connection has no API key. Add it under Settings > Integrations."
        ) from None
    except (DecryptionError, EncryptionConfigError):
        raise PeopleUnavailable("The connection's credentials could not be read.") from None
    except ProviderError as error:
        raise PeopleUnavailable(
            "The connection is not set up correctly (check the API key)."
        ) from error
    names = {
        PEOPLE: ("find_people",),
        EMAIL: ("find_email", "find_email_by_uid"),
        VERIFY: ("verify",),
    }[category]
    for name in names:
        setattr(
            adapter,
            name,
            metered_operation(
                connection, adapter, getattr(adapter, name), campaign=campaign, user=user
            ),
        )
    return adapter


def people_registry() -> AdapterRegistry:
    """The registry of the people, email and verification adapters (for ``provider_task``)."""
    return _registry()


def connection_problem(category: str, client_id: Any) -> str | None:
    """Why ``category`` cannot be used for this client (a safe message), or ``None`` when a
    usable connection of the supported provider exists. Reads no credentials: used to refuse a
    background job at once instead of queueing one that is certain to fail."""
    connection = resolve_connection(client_id, category)
    if connection is None:
        return _message_missing(category)
    if connection.provider != PROVIDER[category]:
        return f"The provider '{connection.provider}' is not supported yet for {_WHAT[category]}."
    return None


def metered_operation(
    connection: Any,
    adapter: Any,
    operation: Any,
    *,
    job: Any = None,
    campaign: Any = None,
    user: Any = None,
) -> Any:
    """``operation`` wrapped so each call logs one ``ProviderCall`` and respects the budget.

    The wrapper keeps the operation's name, so it can go through ``ProviderJobContext.call``
    (retries, rate limit, circuit breaker) and still be metered."""

    @functools.wraps(operation)
    def call(*args: Any, **kwargs: Any) -> Any:
        return tracked_execute(
            connection,
            adapter,
            operation,
            *args,
            job=job,
            campaign=campaign,
            user=user,
            **kwargs,
        )

    return call


def _test(category: str) -> Any:
    def tester(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
        try:
            spec = ConnectionSpec(
                category, connection.provider, connection.config, credentials.reveal()
            )
            adapter = _registry().resolve(spec)
        except ProviderError:
            return False, "The settings are not complete: add the API key."
        result = adapter.test_connection()
        if result.ok:
            return True, "The provider accepted the API key."
        return False, result.message

    return tester


def register_testers() -> None:
    for category, provider in PROVIDER.items():
        register_tester(category, provider, _test(category), replace=True)
