"""Write the audit log: field-level diffs with secrets redacted (issue #44).

Callers (services) take a ``snapshot`` of the object before and after a change and call
``record_change``; it stores only the fields that differ and never a secret. The audit trail is
an explicit service call, not a blanket signal (see ``backend/README.md``).

    before = snapshot(client, CLIENT_FIELDS)
    ... change and save the client ...
    record_change(AuditAction.UPDATE, "client", client.pk, actor=user, client=client,
                  before=before, after=snapshot(client, CLIENT_FIELDS))

Secrets: any key that looks like a password, token, API key, secret, cookie or credential (the
same rule the log redactor uses, ``apps.core.logging.is_sensitive_key``, plus hash/salt/key
names) has its value replaced by ``[REDACTED]`` in both ``before`` and ``after``, at any depth,
and strings are scrubbed for embedded ``key=value`` secrets. The diff is computed first, so a
changed secret still shows up as "changed" without revealing either value.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from .logging import REDACTED, get_request_id, is_sensitive_key, redact_text
from .models import AuditAction, AuditLog

_EXTRA_SECRET_KEY = re.compile(r"(?:^|[_-])(?:hash|salt|key|otp|pin|signature)$", re.IGNORECASE)
_MAX_DEPTH = 8


def is_secret_field(name: object) -> bool:
    return is_sensitive_key(name) or bool(_EXTRA_SECRET_KEY.search(str(name)))


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Recursively mask secret-looking keys and scrub secrets embedded in strings."""
    if _depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(k): REDACTED if is_secret_field(k) else redact_value(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(v, _depth=_depth + 1) for v in value]
    return value


def json_safe(value: Any) -> Any:
    """Turn dates, UUIDs, decimals and the like into plain JSON values."""
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def snapshot(obj: models.Model, fields: Iterable[str]) -> dict[str, Any]:
    """Plain JSON copy of ``fields`` of a model instance (FKs by ``<name>_id``)."""
    out: dict[str, Any] = {}
    for name in fields:
        field = obj._meta.get_field(name)
        attr = field.attname if isinstance(field, models.ForeignKey) else name
        out[attr] = getattr(obj, attr)
    safe: dict[str, Any] = json_safe(out)
    return safe


def diff(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The keys whose value differs, as ``(before_values, after_values)``. A missing key counts
    as ``None``."""
    old, new = before or {}, after or {}
    changed = [k for k in (*old, *(k for k in new if k not in old)) if old.get(k) != new.get(k)]
    return {k: old.get(k) for k in changed}, {k: new.get(k) for k in changed}


def record(
    *,
    action: str,
    object_type: str,
    object_id: uuid.UUID | str,
    actor: Any = None,
    client: Any = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Insert one audit entry as given (redacted, JSON-safe). Prefer ``record_change``."""
    actor_id = getattr(actor, "pk", None) if getattr(actor, "is_authenticated", False) else None
    client_id = getattr(client, "pk", client)
    return AuditLog.objects.create(
        actor_id=actor_id,
        client_id=client_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        before=None if before is None else redact_value(json_safe(dict(before))),
        after=None if after is None else redact_value(json_safe(dict(after))),
        request_id=get_request_id() or "",
    )


def record_change(
    action: str,
    object_type: str,
    object_id: uuid.UUID | str,
    *,
    actor: Any = None,
    client: Any = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> AuditLog | None:
    """Record a change from two snapshots.

    ``create`` stores the whole ``after`` (``before`` is null), ``delete`` the whole ``before``
    (``after`` is null); every other action stores only the differing fields and writes nothing
    (returns ``None``) when nothing differs.
    """
    if action == AuditAction.CREATE:
        before_out, after_out = None, dict(after or {})
    elif action == AuditAction.DELETE:
        before_out, after_out = dict(before or {}), None
    else:
        before_out, after_out = diff(before, after)
        if not after_out:
            return None
    return record(
        action=action,
        object_type=object_type,
        object_id=object_id,
        actor=actor,
        client=client,
        before=before_out,
        after=after_out,
    )
