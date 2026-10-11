"""Small cleaning helpers for provider data (internal to the framework).

Provider data is untrusted. These helpers never guess: a value that is missing, of the wrong type
or out of range becomes ``None`` ("unknown"), never a default. Text is stripped and bounded.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from apps.campaigns.reference import ISO_3166_ALPHA2
from apps.core.logging import redact_text

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}")
_EMAIL = re.compile(
    r"[^@\s]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)

MAX_URL_LENGTH = 2000


def text(value: object, limit: int) -> str | None:
    """Stripped text cut to ``limit`` characters; ``None`` if not a string or blank."""
    if not isinstance(value, str):
        return None
    out = _CONTROL.sub("", value).strip()[:limit]
    return out or None


def scrubbed_text(value: object, limit: int) -> str | None:
    """``text`` with embedded secrets masked (for free text that may echo a payload)."""
    out = text(value, limit)
    return redact_text(out) if out else None


def url(value: object) -> str | None:
    """An http(s) URL without credentials or whitespace, else ``None``. Never fetched."""
    raw = text(value, MAX_URL_LENGTH + 1)
    if raw is None or len(raw) > MAX_URL_LENGTH or re.search(r"\s", raw):
        return None
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return raw


def domain(value: object) -> str | None:
    """A bare lower-case hostname (a leading ``www.`` is dropped), else ``None``."""
    raw = text(value, 255)
    if raw is None:
        return None
    host = raw.lower().removeprefix("www.")
    return host if _DOMAIN.fullmatch(host) else None


def email(value: object) -> str | None:
    raw = text(value, 254)
    if raw is None or not _EMAIL.fullmatch(raw):
        return None
    local, _, host = raw.rpartition("@")
    return f"{local}@{host.lower()}"


def currency(value: object) -> str | None:
    raw = text(value, 10)
    return raw.upper() if raw and len(raw) == 3 and raw.isalpha() else None


def country(value: object) -> str | None:
    raw = text(value, 2)
    if raw is None:
        return None
    code = raw.upper()
    return code if code in ISO_3166_ALPHA2 else None


def integer(value: object, *, minimum: int = 0, maximum: int = 10_000_000) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        return None
    return value


def decimal(
    value: object, *, minimum: Decimal | None = None, maximum: Decimal | None = None
) -> Decimal | None:
    """A finite ``Decimal`` from an int, float, str or Decimal, within bounds; else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not out.is_finite():
        return None
    if (minimum is not None and out < minimum) or (maximum is not None and out > maximum):
        return None
    return out


def day(value: object) -> date | None:
    """A ``date`` (a ``datetime`` is reduced to its date); ``None`` for anything else."""
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def aware(value: object, name: str) -> datetime:
    """``value`` as a timezone-aware datetime or ``ValueError`` (used where it is mandatory)."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def set_attr(obj: Any, name: str, value: Any) -> None:
    """Assign on a frozen dataclass during ``__post_init__``."""
    object.__setattr__(obj, name, value)
