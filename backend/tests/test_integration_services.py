"""Integration connection model and services (issue #76): encryption at rest, audit without
values, write-only credentials, fail closed, resolution and key rotation."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import connection

from apps.core.models import AuditAction, AuditLog
from apps.integrations import crypto, services
from apps.integrations.crypto import Credentials, DecryptionError
from apps.integrations.models import (
    ConnectionStatus,
    IntegrationCategory,
    IntegrationConnection,
)
from tests.factories import make_client, make_user
from tests.factories_integrations import FAKE_SECRET, FAKE_SECRET_2, make_connection

pytestmark = pytest.mark.django_db


def raw_row(connection_obj: IntegrationConnection) -> dict[str, object]:
    """The row exactly as the database stores it (bypasses every field conversion)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT credentials, credentials_hint, config FROM integrations_integrationconnection "
            "WHERE id = %s",
            [connection_obj.pk.hex if connection.vendor == "sqlite" else connection_obj.pk],
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(zip(("credentials", "credentials_hint", "config"), row, strict=True))


def audit_text(obj: IntegrationConnection) -> str:
    rows = AuditLog.objects.filter(object_id=obj.pk).values("before", "after")
    return json.dumps(list(rows), default=str)


# ------------------------------------------------------------------ encryption at rest


def test_the_database_column_holds_ciphertext_only() -> None:
    conn = make_connection()
    raw = raw_row(conn)
    stored = str(raw["credentials"])
    assert stored.startswith("gAAAA")
    for column in raw.values():
        assert FAKE_SECRET not in str(column)
    assert "api_key" not in stored
    # and it still round-trips through the ORM
    fresh = IntegrationConnection.objects.get(pk=conn.pk)
    assert fresh.credentials.get("api_key") == FAKE_SECRET


def test_loading_a_row_does_not_decrypt_and_a_rename_keeps_the_token(settings) -> None:
    conn = make_connection()
    before = raw_row(conn)["credentials"]
    settings.INTEGRATION_ENCRYPTION_KEYS = [crypto.generate_key()]  # the key is gone entirely
    loaded = IntegrationConnection.objects.get(pk=conn.pk)  # listing needs no key
    assert loaded.has_credentials
    loaded.name = "Renamed"
    loaded.save(update_fields=["name"])  # saving a sealed value writes the same token back
    assert raw_row(conn)["credentials"] == before


def test_model_values_never_print_the_secret() -> None:
    conn = make_connection()
    fresh = IntegrationConnection.objects.get(pk=conn.pk)
    shown = "\n".join(
        [
            repr(fresh),
            str(fresh),
            repr(fresh.credentials),
            repr(list(IntegrationConnection.objects.values())),
            repr(IntegrationConnection.objects.filter(pk=conn.pk)),
        ]
    )
    assert FAKE_SECRET not in shown


def test_the_field_refuses_plain_strings_and_filtering_by_plaintext() -> None:
    conn = make_connection()
    conn.credentials = FAKE_SECRET
    with pytest.raises(TypeError):
        conn.save(update_fields=["credentials"])
    with pytest.raises(TypeError):
        IntegrationConnection.objects.filter(credentials=FAKE_SECRET).exists()


def test_a_connection_without_credentials_stores_an_empty_column() -> None:
    conn = make_connection(credentials=None)
    assert raw_row(conn)["credentials"] == ""
    assert not conn.has_credentials
    assert conn.credentials_hint == {}


# ------------------------------------------------------------------ create and update


def test_create_stores_hint_and_audits_without_values() -> None:
    user = make_user()
    client = make_client()
    conn = services.create_connection(
        client,
        user,
        category=IntegrationCategory.COMPANY_DATA,
        provider="fake",
        name="  Main  ",
        config={"region": "eu"},
        credentials={"api_key": FAKE_SECRET, "short": "tiny"},
    )
    assert conn.name == "Main"
    assert conn.created_by == user
    assert conn.credentials_hint == {"api_key": f"****{FAKE_SECRET[-4:]}", "short": "****"}
    entry = AuditLog.objects.get(object_id=conn.pk, action=AuditAction.CREATE)
    assert entry.actor_id == user.pk
    assert entry.client_id == client.pk
    assert entry.after is not None
    assert entry.after["credentials_change"] == "set"
    assert FAKE_SECRET not in audit_text(conn)
    assert "tiny" not in audit_text(conn)


def test_replacing_credentials_audits_only_that_they_changed() -> None:
    user = make_user()
    conn = make_connection()
    services.set_credentials(conn, user, {"api_key": FAKE_SECRET_2})
    entry = AuditLog.objects.filter(object_id=conn.pk, action=AuditAction.UPDATE).latest(
        "created_at"
    )
    assert entry.after == {"credentials_change": "replaced"}
    assert entry.before == {"credentials_change": None}
    assert FAKE_SECRET not in audit_text(conn)
    assert FAKE_SECRET_2 not in audit_text(conn)
    assert conn.credentials.get("api_key") == FAKE_SECRET_2
    assert conn.credentials_hint == {"api_key": f"****{FAKE_SECRET_2[-4:]}"}
    assert conn.credentials_updated_at is not None


def test_update_without_credentials_keeps_them_and_audits_the_diff() -> None:
    user = make_user()
    conn = make_connection()
    services.update_connection(conn, user, name="Other", config={"model": "fake-2"})
    assert conn.credentials.get("api_key") == FAKE_SECRET
    entry = AuditLog.objects.filter(object_id=conn.pk, action=AuditAction.UPDATE).latest(
        "created_at"
    )
    assert entry.after == {"name": "Other", "config": {"model": "fake-2"}}


def test_an_update_that_changes_nothing_writes_no_audit_entry() -> None:
    conn = make_connection()
    count = AuditLog.objects.filter(object_id=conn.pk).count()
    services.update_connection(conn, None, name=conn.name)
    assert AuditLog.objects.filter(object_id=conn.pk).count() == count


def test_clear_credentials() -> None:
    conn = make_connection()
    services.clear_credentials(conn, make_user())
    assert not conn.has_credentials
    assert raw_row(conn)["credentials"] == ""
    assert conn.credentials_hint == {}
    entry = AuditLog.objects.filter(object_id=conn.pk, action=AuditAction.UPDATE).latest(
        "created_at"
    )
    assert entry.after == {"credentials_change": "cleared"}


def test_unknown_fields_and_provider_changes_are_rejected() -> None:
    conn = make_connection()
    with pytest.raises(ValidationError):
        services.update_connection(conn, None, provider="other")
    with pytest.raises(ValidationError):
        services.update_connection(conn, None, category="crm")


@pytest.mark.parametrize(
    "credentials",
    [{}, [], "text", {"": "x"}, {"bad name": "x"}, {"k": ""}, {"k": 5}, {"k": "x" * 5000}],
)
def test_invalid_credentials_are_rejected_without_echoing_them(credentials) -> None:
    with pytest.raises(ValidationError) as info:
        make_connection(credentials=credentials)
    assert "x" * 100 not in str(info.value)


def test_config_may_not_hold_secrets() -> None:
    for config in ({"api_key": "x"}, {"nested": {"password": "x"}}, {"a": [{"token": "x"}]}):
        with pytest.raises(ValidationError):
            make_connection(config=config)
    with pytest.raises(ValidationError):
        make_connection(config=["not", "an", "object"])
    assert make_connection(config={"model": "m", "temperature": 0.2}).config["model"] == "m"


@pytest.mark.parametrize("provider", ["", "Open AI", "1abc", "a" * 65, "with-dash"])
def test_provider_key_is_validated(provider: str) -> None:
    with pytest.raises(ValidationError):
        make_connection(provider=provider)


def test_category_must_be_known() -> None:
    with pytest.raises(ValidationError):
        make_connection(category="telepathy")


def test_names_are_unique_per_client_category_and_provider_ignoring_case() -> None:
    client = make_client()
    make_connection(client, name="Main")
    with pytest.raises(ValidationError) as info:
        make_connection(client, name="main")
    assert "name" in info.value.message_dict
    # other client, other provider, other category: all fine
    make_connection(make_client(), name="Main")
    make_connection(client, name="Main", provider="other")
    make_connection(client, name="Main", category=IntegrationCategory.CRM)


def test_a_new_connection_cannot_start_in_error() -> None:
    with pytest.raises(ValidationError):
        make_connection(status=ConnectionStatus.ERROR)


# ------------------------------------------------------------------ status, archive


def test_disable_enable_archive_restore_are_audited_and_idempotent() -> None:
    user = make_user()
    conn = make_connection()
    services.disable_connection(conn, user)
    assert conn.status == ConnectionStatus.DISABLED
    services.disable_connection(conn, user)  # no second entry
    services.enable_connection(conn, user)
    assert conn.status == ConnectionStatus.ACTIVE
    services.archive_connection(conn, user)
    services.archive_connection(conn, user)
    services.restore_connection(conn, user)
    assert conn.archived_at is None
    actions = list(
        AuditLog.objects.filter(object_id=conn.pk)
        .order_by("created_at")
        .values_list("action", flat=True)
    )
    assert actions == ["create", "update", "update", "archive", "restore"]
    assert FAKE_SECRET not in audit_text(conn)


def test_an_archived_connection_is_read_only() -> None:
    conn = make_connection()
    services.archive_connection(conn)
    for call in (
        lambda: services.update_connection(conn, None, name="x"),
        lambda: services.set_credentials(conn, None, {"api_key": FAKE_SECRET_2}),
        lambda: services.disable_connection(conn),
        lambda: services.clear_credentials(conn),
    ):
        with pytest.raises(ValidationError):
            call()


def test_restore_is_refused_when_the_name_is_taken() -> None:
    client = make_client()
    first = make_connection(client, name="Main")
    services.archive_connection(first)
    make_connection(client, name="Main")
    with pytest.raises(ValidationError) as info:
        services.restore_connection(first)
    assert "name" in info.value.message_dict


# ------------------------------------------------------------------ test results


def test_record_test_result_scrubs_and_moves_status() -> None:
    conn = make_connection()
    services.record_test_result(conn, ok=False, message="401 for api_key=SUPERSECRETVALUE123")
    conn.refresh_from_db()
    assert conn.status == ConnectionStatus.ERROR
    assert conn.last_test_status == "failed"
    assert "SUPERSECRETVALUE123" not in conn.last_test_message
    assert conn.last_tested_at is not None
    services.record_test_result(conn, ok=True)
    conn.refresh_from_db()
    assert conn.status == ConnectionStatus.ACTIVE
    assert conn.last_test_status == "ok"
    services.disable_connection(conn)
    services.record_test_result(conn, ok=False, message="x" * 2000)
    conn.refresh_from_db()
    assert conn.status == ConnectionStatus.DISABLED
    assert len(conn.last_test_message) == 500


# ------------------------------------------------------------------ get_credentials (adapters)


def test_get_credentials_returns_a_wrapper_and_logs_without_values(caplog) -> None:
    conn = make_connection()
    with caplog.at_level(logging.INFO, logger="apps.integrations.access"):
        creds = services.get_credentials(
            IntegrationConnection.objects.get(pk=conn.pk), purpose="company_search"
        )
    assert isinstance(creds, Credentials)
    assert creds.get("api_key") == FAKE_SECRET
    assert FAKE_SECRET not in repr(creds)
    record = next(r for r in caplog.records if r.name == "apps.integrations.access")
    assert record.connection_id == str(conn.pk)
    assert record.purpose == "company_search"
    assert FAKE_SECRET not in caplog.text
    assert FAKE_SECRET not in json.dumps(record.__dict__, default=str)


def test_get_credentials_refuses_archived_and_empty_connections() -> None:
    empty = make_connection(credentials=None)
    with pytest.raises(crypto.CredentialsNotSetError):
        services.get_credentials(empty)
    archived = make_connection()
    services.archive_connection(archived)
    with pytest.raises(services.ConnectionUnavailableError):
        services.get_credentials(archived)


def test_get_credentials_fails_closed_with_a_wrong_key(settings) -> None:
    conn = make_connection()
    settings.INTEGRATION_ENCRYPTION_KEYS = [crypto.generate_key()]
    with pytest.raises(DecryptionError) as info:
        services.get_credentials(IntegrationConnection.objects.get(pk=conn.pk))
    assert FAKE_SECRET not in str(info.value)


# ------------------------------------------------------------------ resolve_connection


def test_resolve_connection_picks_the_oldest_active_one_of_the_client() -> None:
    client, other = make_client(), make_client()
    first = make_connection(client, name="A")
    second = make_connection(client, name="B")
    foreign = make_connection(other, name="A")
    assert services.resolve_connection(client, "llm") == first
    assert services.resolve_connection(other, "llm") == foreign
    services.disable_connection(first)
    assert services.resolve_connection(client, "llm") == second
    services.archive_connection(second)
    assert services.resolve_connection(client, "llm") is None
    assert services.resolve_connection(client, "crm") is None
    assert services.resolve_connection(other, "llm", provider="nope") is None
    with pytest.raises(ValueError, match="Unknown integration category"):
        services.resolve_connection(client, "telepathy")


# ------------------------------------------------------------------ key rotation command


def test_rotate_credentials_reencrypts_with_the_newest_key(settings) -> None:
    old, new = crypto.generate_key(), crypto.generate_key()
    settings.INTEGRATION_ENCRYPTION_KEYS = [old]
    with_creds = make_connection()
    without = make_connection(credentials=None)
    before = raw_row(with_creds)["credentials"]

    settings.INTEGRATION_ENCRYPTION_KEYS = [new, old]
    out = StringIO()
    call_command("rotate_credentials", stdout=out)
    assert "Re-encrypted 1 credential set" in out.getvalue()
    after = raw_row(with_creds)["credentials"]
    assert after != before
    assert raw_row(without)["credentials"] == ""
    entry = AuditLog.objects.filter(object_id=with_creds.pk).latest("created_at")
    assert entry.after == {"credentials_change": "reencrypted"}

    settings.INTEGRATION_ENCRYPTION_KEYS = [new]  # the old key can go now
    fresh = IntegrationConnection.objects.get(pk=with_creds.pk)
    assert fresh.credentials.get("api_key") == FAKE_SECRET


def test_rotate_credentials_dry_run_changes_nothing(settings) -> None:
    conn = make_connection()
    before = raw_row(conn)["credentials"]
    settings.INTEGRATION_ENCRYPTION_KEYS = [
        crypto.generate_key(),
        *settings.INTEGRATION_ENCRYPTION_KEYS,
    ]
    out = StringIO()
    call_command("rotate_credentials", "--dry-run", stdout=out)
    assert "Would re-encrypt 1" in out.getvalue()
    assert raw_row(conn)["credentials"] == before


def test_rotate_credentials_reports_undecryptable_rows_by_id_and_fails(settings) -> None:
    good = make_connection()
    bad = make_connection()
    settings.INTEGRATION_ENCRYPTION_KEYS = [crypto.generate_key()]  # neither is readable now
    with pytest.raises(CommandError) as info:
        call_command("rotate_credentials", stdout=StringIO())
    assert str(bad.pk) in str(info.value)
    assert str(good.pk) in str(info.value)
    assert FAKE_SECRET not in str(info.value)


def test_rotate_credentials_without_keys_fails_closed(settings) -> None:
    make_connection()
    settings.INTEGRATION_ENCRYPTION_KEYS = []
    with pytest.raises(CommandError):
        call_command("rotate_credentials", stdout=StringIO())
