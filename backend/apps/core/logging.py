"""Structured logging helpers: request-ID context, secret redaction and JSON formatting.

Only the standard library is used so this module is safe to import from settings. Wire it up
through ``build_logging_config`` (see ``config/settings``).
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

# Context for the current request / job. Set by the middleware (and later by the job runner).
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_job_id() -> str | None:
    return _job_id.get()


def set_job_id(value: str | None) -> Token[str | None]:
    """Attach a background job ID to every log line emitted in this context."""
    return _job_id.set(value)


def reset_job_id(token: Token[str | None]) -> None:
    _job_id.reset(token)


# ------------------------------------------------------------------ redaction
_SENSITIVE_WORDS = (
    r"password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|"
    r"session(?:id)?|csrf|credential|private[_-]?key"
)
_SENSITIVE_KEY = rf"[\w-]*(?:{_SENSITIVE_WORDS})[\w-]*"
_SENSITIVE_KEY_RE = re.compile(_SENSITIVE_KEY, re.IGNORECASE)
# key=value, key: value, "key": "value" (quoted or bare values, optional auth scheme).
_KEY_VALUE_RE = re.compile(
    rf"""(?P<key>["']?{_SENSITIVE_KEY}["']?\s*[:=]\s*)"""
    r"""(?:(?:Bearer|Basic|Token)\s+)?"""
    r"""(?:"[^"]*"|'[^']*'|[^\s,;&}\]]+)""",
    re.IGNORECASE,
)
_AUTH_SCHEME_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@", re.I)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")


def is_sensitive_key(key: object) -> bool:
    """True if a mapping key / header name looks like it carries a secret."""
    return bool(_SENSITIVE_KEY_RE.fullmatch(str(key)))


def redact_text(text: str) -> str:
    """Mask secrets embedded in free text (headers, key=value pairs, URLs, JWTs)."""
    text = _KEY_VALUE_RE.sub(lambda m: f"{m.group('key')}{REDACTED}", text)
    text = _AUTH_SCHEME_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}:{REDACTED}@", text)
    return _JWT_RE.sub(REDACTED, text)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact a value: sensitive mapping keys are masked, strings are scrubbed."""
    if _depth > 8:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: REDACTED if is_sensitive_key(k) else redact(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(v, _depth=_depth + 1) for v in value]
    return value


# ------------------------------------------------------------------ filters
class RequestContextFilter(logging.Filter):
    """Stamp ``request_id`` and ``job_id`` (from contextvars) on every record.

    Django logs 4xx/5xx responses (``django.request``) after the middleware has unwound, when the
    contextvar is already reset; those records carry the request as ``extra`` so fall back to it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id() or getattr(
            getattr(record, "request", None), "request_id", None
        )
        record.request_id = request_id or "-"
        record.job_id = get_job_id() or "-"
        return True


# Attributes present on every LogRecord (plus ones we add); anything else came from ``extra=``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName", "request_id", "job_id"}
)


class RedactionFilter(logging.Filter):
    """Never let secrets reach a log handler.

    The message is fully formatted and scrubbed (so ``%s`` arguments are covered), and ``extra``
    fields are redacted by key name and by content.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        for key in set(vars(record)) - _STANDARD_ATTRS:
            value = vars(record)[key]
            setattr(record, key, REDACTED if is_sensitive_key(key) else redact(value))
        return True


# ------------------------------------------------------------------ formatters
class RedactingFormatter(logging.Formatter):
    """Human-readable formatter that also scrubs tracebacks."""

    def formatException(self, ei: Any) -> str:
        return redact_text(super().formatException(ei))


class JsonFormatter(RedactingFormatter):
    """One JSON object per line: timestamp, level, logger, message, IDs, extras, exception."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or get_request_id()
        job_id = getattr(record, "job_id", None) or get_job_id()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": None if request_id == "-" else request_id,
        }
        if job_id and job_id != "-":
            payload["job_id"] = job_id
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key != "request" and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = redact_text(self.formatStack(record.stack_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


# ------------------------------------------------------------------ config
def build_logging_config(*, json_logs: bool, level: str = "INFO") -> dict[str, Any]:
    """Return a ``LOGGING`` dict: JSON lines when ``json_logs`` else a readable dev format."""
    formatter: dict[str, Any] = (
        {"()": "apps.core.logging.JsonFormatter"}
        if json_logs
        else {
            "()": "apps.core.logging.RedactingFormatter",
            "format": "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
        }
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_context": {"()": "apps.core.logging.RequestContextFilter"},
            "redact": {"()": "apps.core.logging.RedactionFilter"},
        },
        "formatters": {"default": formatter},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                # Redact first, then the formatter only ever sees clean data.
                "filters": ["redact", "request_context"],
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # Replace Django's own handlers so every line goes through ours exactly once.
            "django": {"handlers": [], "level": level, "propagate": True},
        },
    }
