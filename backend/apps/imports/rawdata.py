"""What an ``ImportRow`` may keep of the submitted data: no credentials, bounded size.

``sanitize_raw_data`` runs in ``ImportRow.save`` so no code path can store an unscrubbed or
oversized payload. It is idempotent (sanitizing its own output changes nothing).

* Values under a sensitive key (password, token, api key, secret, cookie, ...) are replaced by
  ``[REDACTED]``; strings are scrubbed of embedded secrets (``Bearer ...``, ``user:pw@`` URLs,
  JWTs, ``key=value`` pairs) by ``apps.core.logging.redact``.
* Only JSON values are kept (other types become their ``str``), at most ``MAX_KEYS`` keys and
  ``MAX_VALUE_LENGTH`` characters per string.
* The serialized payload is at most ``MAX_RAW_BYTES``. Keys that no longer fit are dropped, in
  order, and ``"_truncated": true`` marks the loss. The submitted file itself is not kept.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from apps.core.logging import redact

MAX_RAW_BYTES = 16 * 1024
MAX_KEYS = 100
MAX_KEY_LENGTH = 100
MAX_VALUE_LENGTH = 2000
TRUNCATED_KEY = "_truncated"
_MAX_DEPTH = 4


def _json_safe(value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value
    if depth >= _MAX_DEPTH:
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(k)[:MAX_KEY_LENGTH]: _json_safe(v, depth + 1)
            for k, v in list(value.items())[:MAX_KEYS]
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(v, depth + 1) for v in list(value)[:MAX_KEYS]]
    return str(value)


def _cap(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_VALUE_LENGTH]
    if isinstance(value, dict):
        return {k: _cap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_cap(v) for v in value]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def sanitize_raw_data(raw: Any) -> dict[str, Any]:
    """Return the storable form of a submitted row: a redacted, size-capped JSON object.

    ``None`` gives ``{}``; anything that is not a mapping is kept under the key ``"value"``.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raw = {"value": raw}
    # Redact whole strings first (a secret must be matched before any cut), then cap lengths.
    safe: dict[str, Any] = _cap(redact(_json_safe(raw)))
    if _size(safe) <= MAX_RAW_BYTES:
        return safe
    kept: dict[str, Any] = {TRUNCATED_KEY: True}
    for key, value in safe.items():
        if key == TRUNCATED_KEY:
            continue
        if _size({**kept, key: value}) > MAX_RAW_BYTES:
            continue
        kept[key] = value
    return kept
