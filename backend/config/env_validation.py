"""Fail-fast validation of required environment variables.

``check_environment`` runs once while settings load. It collects *every* problem and raises a
single ``ImproperlyConfigured`` naming each variable, so a broken deploy is fixed in one pass.
Messages name variables and rules only; values are never included, so secrets cannot leak into
logs or tracebacks. The variable list lives in ``docs/environment.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

PLACEHOLDER_PREFIX = "YOUR_"
MIN_PROD_SECRET_KEY_LENGTH = 32
POSTGRES_SCHEMES = frozenset({"postgres", "postgresql", "postgis"})
BOOL_VALUES = frozenset({"true", "false", "1", "0", "yes", "no", "on", "off"})
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

#: Optional integer variables, with the default the settings use when unset.
INT_VARIABLES: dict[str, int] = {
    "API_PAGE_SIZE": 25,
    "Q_WORKERS": 2,
    "Q_TASK_TIMEOUT": 300,
    "Q_TASK_RETRY": 360,
    "SECURE_HSTS_SECONDS": 3600,
}
#: Optional boolean variables.
BOOL_VARIABLES = ("DEBUG", "SECURE_SSL_REDIRECT")


def _is_placeholder(value: str) -> bool:
    return value.strip().upper().startswith(PLACEHOLDER_PREFIX)


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def validate_environment(environ: Mapping[str, str], *, settings_module: str) -> list[str]:
    """Return one message per missing or invalid variable (empty list when all is well)."""
    problems: list[str] = []
    is_prod = settings_module.endswith(".prod")

    secret_key = environ.get("SECRET_KEY", "")
    if not secret_key.strip():
        problems.append("SECRET_KEY: missing (required)")
    elif _is_placeholder(secret_key):
        problems.append("SECRET_KEY: still a YOUR_* placeholder; set a real random value")
    elif is_prod and len(secret_key) < MIN_PROD_SECRET_KEY_LENGTH:
        problems.append(
            f"SECRET_KEY: too short for production (minimum {MIN_PROD_SECRET_KEY_LENGTH} chars)"
        )

    database_url = environ.get("DATABASE_URL", "")
    if not database_url.strip():
        problems.append("DATABASE_URL: missing (required)")
    elif urlsplit(database_url).scheme not in POSTGRES_SCHEMES:
        problems.append("DATABASE_URL: invalid, expected postgres://USER:PASSWORD@HOST:PORT/NAME")

    if is_prod and not [h for h in environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]:
        problems.append("ALLOWED_HOSTS: missing (required in production, comma-separated)")

    ints: dict[str, int] = {}
    for name, default in INT_VARIABLES.items():
        if name not in environ:
            ints[name] = default
        elif (parsed := _parse_int(environ[name])) is None:
            problems.append(f"{name}: invalid, must be an integer")
        else:
            ints[name] = parsed

    for name in BOOL_VARIABLES:
        if name in environ and environ[name].strip().lower() not in BOOL_VALUES:
            problems.append(f"{name}: invalid, must be true or false")

    if "LOG_LEVEL" in environ and environ["LOG_LEVEL"].strip().upper() not in LOG_LEVELS:
        problems.append(f"LOG_LEVEL: invalid, must be one of {', '.join(sorted(LOG_LEVELS))}")

    if ints["Q_TASK_RETRY"] <= ints["Q_TASK_TIMEOUT"]:
        problems.append("Q_TASK_RETRY: must be greater than Q_TASK_TIMEOUT")

    return problems


def check_environment(environ: Mapping[str, str], *, settings_module: str) -> None:
    """Raise ``ImproperlyConfigured`` listing every problem, or return if the environment is OK."""
    problems = validate_environment(environ, settings_module=settings_module)
    if problems:
        listing = "\n".join(f"  - {problem}" for problem in problems)
        raise ImproperlyConfigured(
            "Invalid environment configuration:\n"
            f"{listing}\n"
            "See docs/environment.md for every variable; copy .env.example to .env to start."
        )
