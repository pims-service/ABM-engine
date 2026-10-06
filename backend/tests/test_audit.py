"""AuditLog: append-only rows, field diffs and secret redaction (issue #44)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.core.audit import (
    diff,
    is_secret_field,
    json_safe,
    record,
    record_change,
    redact_value,
    snapshot,
)
from apps.core.base import ImmutableRecordError
from apps.core.logging import REDACTED, reset_request_id, set_request_id
from apps.core.models import AuditAction, AuditLog
from tests.factories import make_client, make_user
from tests.factories_core import make_audit_log

pytestmark = pytest.mark.django_db

SECRET_KEYS = [
    "password",
    "new_password",
    "api_key",
    "x-api-key",
    "access_token",
    "refresh_token",
    "client_secret",
    "Authorization",
    "private_key",
    "session_id",
    "password_hash",
    "salt",
    "signing_key",
]


def test_diff_keeps_only_changed_keys():
    before, after = diff({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "d": 4})
    assert before == {"b": 2, "c": 3, "d": None}
    assert after == {"b": 5, "c": None, "d": 4}


def test_diff_of_equal_or_empty_input_is_empty():
    assert diff({"a": 1}, {"a": 1}) == ({}, {})
    assert diff(None, None) == ({}, {})


def test_json_safe_converts_rich_values():
    value = {
        "id": uuid.UUID(int=1),
        "when": dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.UTC),
        "day": dt.date(2026, 1, 2),
        "amount": Decimal("1.5"),
    }
    assert json_safe(value) == {
        "id": "00000000-0000-0000-0000-000000000001",
        "when": "2026-01-02T03:04:00Z",
        "day": "2026-01-02",
        "amount": "1.5",
    }


@pytest.mark.parametrize("key", SECRET_KEYS)
def test_secret_looking_keys_are_redacted(key):
    assert is_secret_field(key)
    assert redact_value({key: "s3cr3t-value"}) == {key: REDACTED}  # pragma: allowlist secret


@pytest.mark.parametrize("key", ["name", "notes", "status", "archived_at", "current_profile_id"])
def test_ordinary_keys_survive(key):
    assert not is_secret_field(key)
    assert redact_value({key: "keep me"}) == {key: "keep me"}


def test_redaction_is_recursive_and_scrubs_strings():
    value = {
        "outer": {"inner": [{"token": "abc"}, "api_key=xyz and more"]},  # pragma: allowlist secret
        "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": "x"}}}}}}}}},
    }
    out = redact_value(value)
    assert out["outer"]["inner"][0] == {"token": REDACTED}
    assert "xyz" not in out["outer"]["inner"][1]
    assert "and more" in out["outer"]["inner"][1]
    assert REDACTED in str(out["deep"])  # too deep: collapsed, never stored


def test_record_stores_redacted_json_and_context():
    user, client = make_user(), make_client()
    object_id = uuid.uuid4()
    token = set_request_id("req-123")
    try:
        row = record(
            action=AuditAction.UPDATE,
            object_type="integration",
            object_id=object_id,
            actor=user,
            client=client,
            before={"api_key": "old-key", "name": "A"},  # pragma: allowlist secret
            after={"api_key": "new-key", "name": "B"},  # pragma: allowlist secret
        )
    finally:
        reset_request_id(token)
    row.refresh_from_db()
    assert row.before == {"api_key": REDACTED, "name": "A"}
    assert row.after == {"api_key": REDACTED, "name": "B"}
    assert "new-key" not in str(row.after)
    assert "old-key" not in str(row.before)
    assert (row.actor, row.client, row.object_id, row.request_id) == (
        user,
        client,
        object_id,
        "req-123",
    )
    assert row.created_at is not None
    assert str(row) == f"update integration {object_id}"


def test_changed_secret_still_shows_as_changed_without_values():
    row = record_change(
        AuditAction.UPDATE,
        "user",
        uuid.uuid4(),
        before={"password": "hash-1", "name": "A"},  # pragma: allowlist secret
        after={"password": "hash-2", "name": "A"},  # pragma: allowlist secret
    )
    assert row is not None
    assert row.before == {"password": REDACTED}
    assert row.after == {"password": REDACTED}


def test_unchanged_secret_is_not_logged_at_all():
    row = record_change(
        AuditAction.UPDATE,
        "user",
        uuid.uuid4(),
        before={"password": "same", "name": "A"},  # pragma: allowlist secret
        after={"password": "same", "name": "B"},  # pragma: allowlist secret
    )
    assert row is not None
    assert row.before == {"name": "A"}
    assert row.after == {"name": "B"}


def test_no_changes_writes_nothing():
    assert (
        record_change(AuditAction.UPDATE, "client", uuid.uuid4(), before={"a": 1}, after={"a": 1})
        is None
    )
    assert AuditLog.objects.count() == 0


def test_create_keeps_whole_after_and_delete_whole_before():
    created = record_change(
        AuditAction.CREATE, "client", uuid.uuid4(), after={"name": "A", "notes": ""}
    )
    assert created is not None
    assert created.before is None
    assert created.after == {"name": "A", "notes": ""}
    deleted = record_change(AuditAction.DELETE, "client", uuid.uuid4(), before={"name": "A"})
    assert deleted is not None
    assert deleted.after is None
    assert deleted.before == {"name": "A"}


def test_system_actor_and_anonymous_actor_are_null():
    from django.contrib.auth.models import AnonymousUser

    assert make_audit_log().actor is None
    assert make_audit_log(actor=AnonymousUser()).actor is None
    assert make_audit_log(actor=make_user()).actor is not None


def test_snapshot_uses_attnames_for_foreign_keys():
    client = make_client(notes="hi")
    snap = snapshot(client, ("name", "notes", "created_by", "archived_at"))
    assert snap == {"name": client.name, "notes": "hi", "created_by_id": None, "archived_at": None}


# ------------------------------------------------------------------ append-only


def test_audit_rows_cannot_be_changed_or_deleted():
    row = make_audit_log(after={"name": "A"})
    row.after = {"name": "tampered"}
    with pytest.raises(ImmutableRecordError):
        row.save()
    with pytest.raises(ImmutableRecordError):
        row.delete()
    with pytest.raises(ImmutableRecordError):
        AuditLog.objects.update(action="delete")
    with pytest.raises(ImmutableRecordError):
        AuditLog.objects.all().delete()
    with pytest.raises(ImmutableRecordError):
        AuditLog.objects.bulk_update([row], ["after"])
    assert AuditLog.objects.get(pk=row.pk).after == {"name": "A"}


def test_action_check_constraint():
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditLog.objects.create(action="rename", object_type="client", object_id=uuid.uuid4())


def test_scoping_and_object_lookup():
    a, b = make_client(), make_client()
    object_id = uuid.uuid4()
    make_audit_log(client=a, object_id=object_id)
    make_audit_log(client=b)
    make_audit_log(client=None)
    assert AuditLog.objects.for_client(a).count() == 1
    assert AuditLog.objects.for_object("client", object_id).count() == 1
    admin = make_user(is_superuser=True, is_staff=True)
    assert AuditLog.objects.for_user(admin).count() == 3
    assert AuditLog.objects.for_user(make_user()).count() == 0  # fails closed
