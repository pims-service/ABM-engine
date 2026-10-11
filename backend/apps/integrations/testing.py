"""Connection tests: a small registry of "can this connection work?" checks (issue #77).

A *tester* is a callable ``(connection, credentials) -> (ok, message)`` registered for a
``(category, provider)`` pair. The real adapters (issue #74) register theirs here; until then
only the built-in ``fake`` provider has one, and every other provider honestly reports
``unconfigured`` ("No test available for this provider yet."), never a pretend success.

Testers receive the decrypted :class:`~apps.integrations.crypto.Credentials` for the duration of
the call. They must not log, store or return a secret. Their message is untrusted: callers
scrub it (``services.record_test_result``) and the UI renders it as plain text. An exception
inside a tester becomes a generic failure; its text is never shown (it could hold a secret).

``fake`` is deterministic and offline, for demos, the e2e suite and local development. Its
non-secret ``config["simulate"]`` picks the outcome: ``auth_failed``, ``rate_limited`` or
``error``; anything else (or nothing) succeeds.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.core.audit import record_change
from apps.core.logging import redact_text
from apps.core.models import AuditAction

from . import services
from .crypto import Credentials, CredentialsNotSetError, DecryptionError, EncryptionConfigError
from .models import MAX_TEST_MESSAGE_LENGTH, IntegrationCategory, IntegrationConnection

logger = logging.getLogger(__name__)

Tester = Callable[[IntegrationConnection, Credentials], tuple[bool, str]]

RESULT_OK: Final = "ok"
RESULT_FAILED: Final = "failed"
RESULT_UNCONFIGURED: Final = "unconfigured"

UNCONFIGURED_MESSAGE: Final = "No test available for this provider yet."
TEST_CRASHED_MESSAGE: Final = "The test could not be completed."

_registry: dict[tuple[str, str], Tester] = {}


@dataclass(frozen=True)
class TestOutcome:
    """What a test run produced. ``result`` is ``ok``, ``failed`` or ``unconfigured``."""

    __test__ = False  # not a pytest class

    result: str
    message: str

    @property
    def ok(self) -> bool:
        return self.result == RESULT_OK


def register_tester(category: str, provider: str, tester: Tester, *, replace: bool = False) -> None:
    """Register ``tester`` for a category and provider key (adapters call this on import)."""
    if category not in IntegrationCategory.values:
        raise ValueError(f"Unknown integration category '{category}'.")
    key = (category, provider)
    if key in _registry and not replace:
        raise ValueError(f"A tester for {category}/{provider} is already registered.")
    _registry[key] = tester


def unregister_tester(category: str, provider: str) -> None:
    _registry.pop((category, provider), None)


def get_tester(category: str, provider: str) -> Tester | None:
    return _registry.get((category, provider))


def run_test(connection: IntegrationConnection, credentials: Credentials) -> TestOutcome:
    """Run the registered tester. Never raises; never pretends a missing tester succeeded."""
    tester = get_tester(connection.category, connection.provider)
    if tester is None:
        return TestOutcome(RESULT_UNCONFIGURED, UNCONFIGURED_MESSAGE)
    try:
        ok, message = tester(connection, credentials)
    except Exception as exc:
        # The exception text may echo a request; log only its type.
        logger.warning(
            "integration test crashed",
            extra={"connection_id": str(connection.pk), "error_type": type(exc).__name__},
        )
        return TestOutcome(RESULT_FAILED, TEST_CRASHED_MESSAGE)
    safe = redact_text(str(message))[:MAX_TEST_MESSAGE_LENGTH]  # what is stored is what is shown
    return TestOutcome(RESULT_OK if ok else RESULT_FAILED, safe)


def check_connection(connection: IntegrationConnection, user: User | None = None) -> TestOutcome:
    """Test a connection, store the result and audit it. Used by the test endpoint.

    ``ok`` and ``failed`` are stored with ``services.record_test_result`` (message scrubbed); a
    failure moves an active connection to ``error``. ``unconfigured`` changes nothing, because
    nothing was tested. The audit entry holds the result word only, never the message or a secret.
    """
    if connection.is_archived:
        raise ValidationError("Archived connections cannot be tested; restore it first.")
    if get_tester(connection.category, connection.provider) is None:
        outcome = TestOutcome(RESULT_UNCONFIGURED, UNCONFIGURED_MESSAGE)  # no secret is touched
    else:
        try:
            credentials = services.get_credentials(connection, purpose="connection_test", user=user)
        except CredentialsNotSetError:
            outcome = TestOutcome(RESULT_FAILED, "No credentials are stored for this connection.")
        except (DecryptionError, EncryptionConfigError):
            outcome = TestOutcome(RESULT_FAILED, "The stored credentials cannot be read.")
        else:
            outcome = run_test(connection, credentials)
    before = {"status": connection.status, "last_test_status": connection.last_test_status}
    if outcome.result != RESULT_UNCONFIGURED:
        services.record_test_result(connection, ok=outcome.ok, message=outcome.message)
    record_change(
        AuditAction.UPDATE,
        services.AUDIT_OBJECT_TYPE,
        connection.pk,
        actor=user,
        client=connection.client_id,
        before=before,
        after={
            "status": connection.status,
            "last_test_status": connection.last_test_status,
            "connection_test": outcome.result,
        },
    )
    return outcome


# ------------------------------------------------------------------ the built-in fake provider

_FAKE_FAILURES = {
    "auth_failed": "Authentication failed: the provider rejected the credentials (simulated).",
    "rate_limited": "Rate limited: the provider asked to try again later (simulated).",
    "error": "The provider returned an error (simulated).",
}


def _fake_tester(connection: IntegrationConnection, credentials: Credentials) -> tuple[bool, str]:
    simulate = connection.config.get("simulate") if isinstance(connection.config, dict) else None
    if isinstance(simulate, str) and simulate in _FAKE_FAILURES:
        return False, _FAKE_FAILURES[simulate]
    return True, "Fake provider: connection OK."


for _category in IntegrationCategory.values:
    register_tester(_category, "fake", _fake_tester)
