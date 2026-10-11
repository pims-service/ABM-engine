"""Factory for integration connections (issue #76). Kept apart from `tests/factories.py`."""

from __future__ import annotations

from typing import Any

from apps.accounts.models import User
from apps.campaigns.models import Client
from apps.integrations import services
from apps.integrations.models import IntegrationCategory, IntegrationConnection
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
