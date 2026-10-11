"""Write path for integration connections and their credentials (issue #76).

These functions are the only supported way to create or change a connection. Each one writes an
audit entry (``apps.core.audit``) in the same transaction, naming the acting user. Audit entries
never hold a secret: a credentials change shows only ``credentials_change: "replaced"`` (or
``set`` / ``cleared``). That key is allow-listed in ``apps.core.audit.SAFE_MARKERS``, because
any key containing "credential" is otherwise redacted.

Credentials are **write-only**. After ``set_credentials`` nothing returns the plaintext except
:func:`get_credentials`, which adapters call right before an outbound request. It returns a
:class:`~apps.integrations.crypto.Credentials` wrapper (never a plain dict, so a stray ``print``
or traceback stays safe), refuses archived connections and logs each read (connection, category,
provider, purpose and user, never a value) to the ``apps.integrations.access`` logger.

Permission checks belong to the API layer; these functions only enforce data rules.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Client
from apps.core.audit import is_secret_field, record_change, snapshot
from apps.core.logging import redact_text
from apps.core.models import AuditAction

from . import crypto
from .crypto import Credentials, CredentialsNotSetError, mask_all
from .models import (
    MAX_TEST_MESSAGE_LENGTH,
    ConnectionStatus,
    ConnectionTestStatus,
    IntegrationCategory,
    IntegrationConnection,
)

access_log = logging.getLogger("apps.integrations.access")

AUDIT_OBJECT_TYPE = "integration_connection"
CONNECTION_AUDIT_FIELDS = ("category", "provider", "name", "status", "config", "archived_at")
EDITABLE_FIELDS = ("name", "config")

NAME_TAKEN = "This client already has an active connection with that provider and name."
MAX_CONFIG_BYTES = 16_384
MAX_CREDENTIAL_ENTRIES = 20
MAX_CREDENTIAL_VALUE_LENGTH = 4096
CREDENTIAL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class ConnectionUnavailableError(RuntimeError):
    """The connection is archived, so adapters must not use its credentials."""


# ------------------------------------------------------------------ validation


def validate_config(config: Any) -> dict[str, Any]:
    """Non-secret settings: a JSON object, bounded in size, with no secret-looking key.

    The secret-key rule keeps keys out of the plain JSON column and out of audit diffs (which
    show ``config`` as is). Put them in ``credentials``.
    """
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValidationError({"config": "Config must be a JSON object."})
    if len(repr(config)) > MAX_CONFIG_BYTES:
        raise ValidationError({"config": "Config is too large."})

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 6:
            raise ValidationError({"config": "Config is nested too deeply."})
        if isinstance(value, dict):
            for key, inner in value.items():
                if is_secret_field(key):
                    raise ValidationError(
                        {"config": "Config must not hold secrets; set them as credentials."}
                    )
                walk(inner, depth + 1)
        elif isinstance(value, list):
            for inner in value:
                walk(inner, depth + 1)

    walk(config)
    return dict(config)


def validate_credentials(values: Any) -> dict[str, str]:
    """A non-empty ``{name: secret}`` mapping of strings. Errors never echo a value."""
    if not isinstance(values, Mapping) or not values:
        raise ValidationError({"credentials": "Provide credentials as a non-empty object."})
    if len(values) > MAX_CREDENTIAL_ENTRIES:
        raise ValidationError({"credentials": "Too many credential entries."})
    clean: dict[str, str] = {}
    for name, secret in values.items():
        if not isinstance(name, str) or not CREDENTIAL_NAME_RE.match(name):
            raise ValidationError(
                {"credentials": "Credential names are letters, digits and underscores."}
            )
        if not isinstance(secret, str) or not secret.strip():
            raise ValidationError({"credentials": f"Credential '{name}' must be a non-empty text."})
        if len(secret) > MAX_CREDENTIAL_VALUE_LENGTH:
            raise ValidationError({"credentials": f"Credential '{name}' is too long."})
        clean[name] = secret.strip()
    return clean


# ------------------------------------------------------------------ helpers


def _audit_snapshot(connection: IntegrationConnection) -> dict[str, Any]:
    """Non-secret fields only. Credential changes are added by the callers as a marker."""
    return snapshot(connection, CONNECTION_AUDIT_FIELDS)


def _name_taken(connection: IntegrationConnection) -> bool:
    qs = (
        IntegrationConnection.objects.active()
        .filter(
            client_id=connection.client_id,
            category=connection.category,
            provider=connection.provider,
        )
        .annotate(lowered=Lower("name"))
        .filter(lowered=connection.name.lower())
    )
    if connection._state.adding is False:
        qs = qs.exclude(pk=connection.pk)
    return qs.exists()


def _save_checked(connection: IntegrationConnection, **save_kwargs: Any) -> None:
    """``save`` mapping a lost race on the unique name constraint to a field error."""
    try:
        with transaction.atomic():
            connection.save(**save_kwargs)
    except IntegrityError as exc:
        if "integrations_connection_name_unique_active" not in str(exc):
            raise
        raise ValidationError({"name": NAME_TAKEN}) from exc


def _set_credentials_on(connection: IntegrationConnection, values: Mapping[str, str]) -> None:
    clean = validate_credentials(values)
    connection.credentials = Credentials(clean)
    connection.credentials_hint = mask_all(clean)
    connection.credentials_updated_at = timezone.now()


def _change(
    connection: IntegrationConnection,
    action: str,
    user: User | None,
    mutate: Callable[[IntegrationConnection], None],
    *,
    marker: str | None = None,
) -> IntegrationConnection:
    """Lock the row, run ``mutate(locked)``, audit the diff, sync ``connection``."""
    with transaction.atomic():
        locked = IntegrationConnection.objects.select_for_update().get(pk=connection.pk)
        before = _audit_snapshot(locked)
        mutate(locked)
        after = _audit_snapshot(locked)
        if marker:
            after["credentials_change"] = marker
        record_change(
            action,
            AUDIT_OBJECT_TYPE,
            locked.pk,
            actor=user,
            client=locked.client_id,
            before=before,
            after=after,
        )
    connection.refresh_from_db()
    return connection


def _ensure_editable(connection: IntegrationConnection) -> None:
    if connection.is_archived:
        raise ValidationError("Archived connections are read-only; restore it first.")


# ------------------------------------------------------------------ create and update


@transaction.atomic
def create_connection(
    client: Client,
    user: User | None,
    *,
    category: str,
    provider: str,
    name: str,
    config: Mapping[str, Any] | None = None,
    credentials: Mapping[str, str] | None = None,
    status: str = ConnectionStatus.ACTIVE,
) -> IntegrationConnection:
    """Create a connection (optionally with its first credentials) and audit it."""
    connection = IntegrationConnection(
        client=client,
        category=category,
        provider=provider.strip(),
        name=name.strip(),
        config=validate_config(config),
        status=status,
        created_by=user,
    )
    if status == ConnectionStatus.ERROR:
        raise ValidationError({"status": "A new connection starts active or disabled."})
    if credentials is not None:
        _set_credentials_on(connection, credentials)
    connection.full_clean(validate_constraints=False)
    if _name_taken(connection):
        raise ValidationError({"name": NAME_TAKEN})
    _save_checked(connection)
    after = _audit_snapshot(connection)
    if credentials is not None:
        after["credentials_change"] = "set"
    record_change(
        AuditAction.CREATE,
        AUDIT_OBJECT_TYPE,
        connection.pk,
        actor=user,
        client=connection.client_id,
        after=after,
    )
    return connection


def update_connection(
    connection: IntegrationConnection,
    user: User | None = None,
    *,
    credentials: Mapping[str, str] | None = None,
    **changes: Any,
) -> IntegrationConnection:
    """Change ``name``, ``config`` and/or replace the credentials. Category and provider are fixed
    (create a new connection to switch provider). No audit entry when nothing changed."""
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown field."))

    def mutate(locked: IntegrationConnection) -> None:
        _ensure_editable(locked)
        fields = ["updated_at"]
        if "name" in changes:
            locked.name = str(changes["name"]).strip()
            fields.append("name")
        if "config" in changes:
            locked.config = validate_config(changes["config"])
            fields.append("config")
        if credentials is not None:
            _set_credentials_on(locked, credentials)
            fields += ["credentials", "credentials_hint", "credentials_updated_at"]
        locked.full_clean(validate_constraints=False)
        if "name" in changes and _name_taken(locked):
            raise ValidationError({"name": NAME_TAKEN})
        _save_checked(locked, update_fields=fields)

    return _change(
        connection,
        AuditAction.UPDATE,
        user,
        mutate,
        marker="replaced" if credentials is not None else None,
    )


def set_credentials(
    connection: IntegrationConnection, user: User | None, credentials: Mapping[str, str]
) -> IntegrationConnection:
    """Replace the credentials (write-only). Audited as ``credentials_change: replaced``."""
    return update_connection(connection, user, credentials=credentials)


def clear_credentials(
    connection: IntegrationConnection, user: User | None = None
) -> IntegrationConnection:
    """Remove the stored credentials (the connection keeps its settings)."""

    def mutate(locked: IntegrationConnection) -> None:
        _ensure_editable(locked)
        locked.credentials = Credentials.empty()
        locked.credentials_hint = {}
        locked.credentials_updated_at = timezone.now()
        locked.save(
            update_fields=[
                "credentials",
                "credentials_hint",
                "credentials_updated_at",
                "updated_at",
            ]
        )

    return _change(connection, AuditAction.UPDATE, user, mutate, marker="cleared")


# ------------------------------------------------------------------ status


def _set_status(status: str) -> Callable[[IntegrationConnection], None]:
    def mutate(locked: IntegrationConnection) -> None:
        _ensure_editable(locked)
        locked.status = status
        locked.save(update_fields=["status", "updated_at"])

    return mutate


def disable_connection(
    connection: IntegrationConnection, user: User | None = None
) -> IntegrationConnection:
    """Stop adapters from resolving this connection. Idempotent."""
    return _change(connection, AuditAction.UPDATE, user, _set_status(ConnectionStatus.DISABLED))


def enable_connection(
    connection: IntegrationConnection, user: User | None = None
) -> IntegrationConnection:
    """Back to ``active`` (also clears an ``error`` status). Idempotent."""
    return _change(connection, AuditAction.UPDATE, user, _set_status(ConnectionStatus.ACTIVE))


def archive_connection(
    connection: IntegrationConnection, user: User | None = None
) -> IntegrationConnection:
    """Archive (the soft delete). Credentials stay encrypted until the row is purged by hand."""
    return _change(connection, AuditAction.ARCHIVE, user, lambda locked: locked.archive())


def restore_connection(
    connection: IntegrationConnection, user: User | None = None
) -> IntegrationConnection:
    """Restore an archived connection. Refused if an active one now uses its name."""

    def mutate(locked: IntegrationConnection) -> None:
        if locked.is_archived:
            if _name_taken(locked):  # looks at active rows only, so it ignores this archived one
                raise ValidationError({"name": NAME_TAKEN})
            locked.restore()

    return _change(connection, AuditAction.RESTORE, user, mutate)


def record_test_result(
    connection: IntegrationConnection, *, ok: bool, message: str = ""
) -> IntegrationConnection:
    """Store the outcome of a connection test (used by adapters and the settings UI).

    ``message`` is scrubbed of secret-looking text and truncated; pass the provider's error text,
    never the request. A failure moves the connection to ``error``; a success moves ``error``
    back to ``active``. A disabled connection stays disabled.
    """
    safe = redact_text(message or "")[:MAX_TEST_MESSAGE_LENGTH]
    connection.last_tested_at = timezone.now()
    connection.last_test_status = ConnectionTestStatus.OK if ok else ConnectionTestStatus.FAILED
    connection.last_test_message = safe
    fields = ["last_tested_at", "last_test_status", "last_test_message", "updated_at"]
    if not ok and connection.status == ConnectionStatus.ACTIVE:
        connection.status = ConnectionStatus.ERROR
        fields.append("status")
    elif ok and connection.status == ConnectionStatus.ERROR:
        connection.status = ConnectionStatus.ACTIVE
        fields.append("status")
    connection.save(update_fields=fields)
    return connection


# ------------------------------------------------------------------ reading (adapters only)


def resolve_connection(
    client: Client | uuid.UUID | str,
    category: str,
    provider: str | None = None,
) -> IntegrationConnection | None:
    """The connection an adapter should use for ``category`` (optionally a given ``provider``).

    Only active, non-archived connections count. When several match, the oldest wins, so
    adding a second one never silently changes behaviour; disable or archive the first to switch.
    """
    if category not in IntegrationCategory.values:
        raise ValueError(f"Unknown integration category '{category}'.")
    qs = IntegrationConnection.objects.for_client(client).usable().filter(category=category)
    if provider is not None:
        qs = qs.filter(provider=provider)
    return qs.order_by("created_at", "id").first()


def get_credentials(
    connection: IntegrationConnection, *, purpose: str = "", user: User | None = None
) -> Credentials:
    """The decrypted credentials, for an adapter to use right now. Never store or log them.

    ``purpose`` is a short label such as ``"company_search"`` for the access log. Raises
    ``ConnectionUnavailableError`` (archived), ``CredentialsNotSetError`` (none stored) and
    ``crypto.DecryptionError`` / ``crypto.EncryptionConfigError`` (fail closed; the message is
    safe to show). The returned object prints as ``Credentials([redacted])``; call ``.get(name)``
    or ``.reveal()`` on the spot.
    """
    if connection.is_archived:
        raise ConnectionUnavailableError("This integration connection is archived.")
    stored = connection.credentials
    if not stored.is_set:
        raise CredentialsNotSetError("This integration connection has no credentials.")
    stored.reveal()  # fail closed now (wrong key, tampering) rather than at first use
    access_log.info(
        "integration credentials read",
        extra={
            "connection_id": str(connection.pk),
            "client_id": str(connection.client_id),
            "category": connection.category,
            "provider": connection.provider,
            "purpose": purpose,
            "user_id": str(user.pk) if user is not None else None,
        },
    )
    return stored  # type: ignore[no-any-return]


# ------------------------------------------------------------------ key rotation


def rotate_all_credentials(*, dry_run: bool = False) -> tuple[int, list[uuid.UUID]]:
    """Re-encrypt every stored credential with the newest key.

    Returns ``(rotated, failed_ids)``. A row that cannot be decrypted is left untouched and
    reported by id only. Each rotated row is audited as ``credentials_change: reencrypted``.
    """
    crypto.get_cipher()  # fail early when no key is configured
    rotated = 0
    failed: list[uuid.UUID] = []
    ids = list(
        IntegrationConnection._base_manager.exclude(credentials=Credentials.empty())
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for pk in ids:
        with transaction.atomic():
            row = IntegrationConnection._base_manager.select_for_update().get(pk=pk)
            token = row.credentials.token()
            if not token:
                continue
            try:
                new_token = crypto.reencrypt_token(token)
            except crypto.DecryptionError:
                failed.append(pk)
                continue
            if dry_run:
                rotated += 1
                continue
            IntegrationConnection._base_manager.filter(pk=pk).update(
                credentials=Credentials.sealed(new_token)
            )
            record_change(
                AuditAction.UPDATE,
                AUDIT_OBJECT_TYPE,
                pk,
                client=row.client_id,
                after={"credentials_change": "reencrypted"},
            )
            rotated += 1
    return rotated, failed
