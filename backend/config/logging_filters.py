"""Logging filter that redacts secret-looking values before a record is emitted."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"
DEFAULT_SECRET_FIELDS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "token",
    "private_key",
    "authorization",
    "database_url",
)
_VALUE = r"""("[^"]*"|'[^']*'|(?:Bearer|Basic)\s+[^\s,;&}]+|[^\s,;&}"']+)"""
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@]+:)[^@\s/]+(@)")


def build_pattern(fields: Iterable[str]) -> re.Pattern[str]:
    """Match ``name=value``, ``name: value`` and ``"name": "value"`` for the given field names.

    A field name also matches inside longer names (``access_token``, ``DB_PASSWORD``).
    """
    names = "|".join(re.escape(f) for f in sorted({f.lower() for f in fields}, key=len, reverse=True))
    return re.compile(rf"""(["']?[\w-]*(?:{names})[\w-]*["']?\s*[:=]\s*){_VALUE}""", re.IGNORECASE)


class SecretScrubbingFilter(logging.Filter):
    """Replace values of common secret fields and URL passwords with ``[REDACTED]``."""

    def __init__(self, name: str = "", extra_fields: Iterable[str] = ()) -> None:
        super().__init__(name)
        self._pattern = build_pattern([*DEFAULT_SECRET_FIELDS, *extra_fields])

    def scrub(self, text: str) -> str:
        text = self._pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
        return _URL_CREDENTIALS.sub(rf"\1{REDACTED}\2", text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.scrub(record.getMessage())
        record.args = None
        return True
