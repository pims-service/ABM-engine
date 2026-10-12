"""Factory for integration connections (issue #76). Kept apart from `tests/factories.py`."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Client
from apps.integrations import services, usage
from apps.integrations.models import (
    CachedResponse,
    IntegrationCategory,
    IntegrationConnection,
    ProviderCall,
    RawPayload,
    UsageBudget,
)
from tests.factories import make_client

#: Fake long secrets (not real). Tests assert they never show up anywhere but the encrypted column.
FAKE_SECRET = "UNITTESTONLYSECRET9f8e7d6c5b4a3210"  # pragma: allowlist secret
FAKE_SECRET_2 = "SECONDTESTONLYSECRETa1b2c3d4e5f60789"  # pragma: allowlist secret

_counter = iter(range(1, 1_000_000))


def make_connection(
    client: Client | None = None, user: User | None = None, **overrides: Any
) -> IntegrationConnection:
    """A persisted connection through the service layer (so it is audited like production).

    Defaults: an active ``llm``/``fake`` connection with ``{"api_key": FAKE_SECRET}``. Pass
    ``credentials=None`` for a connection without credentials.
    """
    params: dict[str, Any] = {
        "category": IntegrationCategory.LLM,
        "provider": "fake",
        "name": f"Connection {next(_counter)}",
        "config": {"model": "fake-1"},
        "credentials": {"api_key": FAKE_SECRET},
    }
    params.update(overrides)
    return services.create_connection(client or make_client(), user, **params)


# ------------------------------------------------------------------ usage and cache (#79, #80)


def make_provider_call(
    connection: IntegrationConnection | None = None, **overrides: Any
) -> ProviderCall:
    """A persisted ``ProviderCall`` (ok, 1 credit by default) on ``connection`` (or a new one)."""
    connection = connection or make_connection()
    params: dict[str, Any] = {
        "connection": connection,
        "category": connection.category,
        "provider": connection.provider,
        "operation": "enrich_company",
        "status": "ok",
        "credits": Decimal("1"),
    }
    params.update(overrides)
    row = ProviderCall(**params)
    row.save()
    return row


def make_budget(connection: IntegrationConnection | None = None, **overrides: Any) -> UsageBudget:
    connection = connection or make_connection()
    params: dict[str, Any] = {"credit_limit": Decimal("100"), "warn_at_percent": 80}
    params.update(overrides)
    return usage.set_budget(connection, None, **params)


def make_cached_response(
    connection: IntegrationConnection | None = None, **overrides: Any
) -> CachedResponse:
    connection = connection or make_connection()
    params: dict[str, Any] = {
        "connection": connection,
        "category": connection.category,
        "provider": connection.provider,
        "operation": "enrich_company",
        "cache_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "payload": {"v": 1},
        "size_bytes": 8,
        "expires_at": timezone.now() + timedelta(days=1),
    }
    params.update(overrides)
    return CachedResponse.objects.create(**params)


def make_raw_payload(call: ProviderCall | None = None, **overrides: Any) -> RawPayload:
    call = call or make_provider_call()
    params: dict[str, Any] = {
        "call": call,
        "payload": {"hello": "world"},
        "size_bytes": 17,
        "expires_at": timezone.now() + timedelta(days=30),
    }
    params.update(overrides)
    return RawPayload.objects.create(**params)
