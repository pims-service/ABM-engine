"""Startup validation of environment variables (fail fast, name variables, never leak values)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.env_validation import check_environment, validate_environment

DEV = "config.settings.dev"
PROD = "config.settings.prod"
GOOD_KEY = "k" * 40  # throwaway, not a real secret
GOOD = {
    "SECRET_KEY": GOOD_KEY,
    "DATABASE_URL": "postgres://u:p@localhost:5432/db",  # pragma: allowlist secret
    "ALLOWED_HOSTS": "example.test",
}


def _env(**overrides: str | None) -> dict[str, str]:
    merged: dict[str, str | None] = {**GOOD, **overrides}
    return {k: v for k, v in merged.items() if v is not None}


def test_valid_environment_has_no_problems() -> None:
    assert validate_environment(_env(), settings_module=DEV) == []
    assert validate_environment(_env(), settings_module=PROD) == []


def test_all_missing_required_variables_are_listed_together() -> None:
    problems = validate_environment({}, settings_module=DEV)
    assert problems == ["SECRET_KEY: missing (required)", "DATABASE_URL: missing (required)"]


def test_blank_secret_key_counts_as_missing() -> None:
    assert validate_environment(_env(SECRET_KEY="  "), settings_module=DEV) == [
        "SECRET_KEY: missing (required)"
    ]


def test_placeholder_secret_key_is_rejected() -> None:
    problems = validate_environment(_env(SECRET_KEY="YOUR_SECRET_KEY"), settings_module=DEV)
    assert problems == ["SECRET_KEY: still a YOUR_* placeholder; set a real random value"]


def test_short_secret_key_only_rejected_in_production() -> None:
    assert validate_environment(_env(SECRET_KEY="short"), settings_module=DEV) == []
    problems = validate_environment(_env(SECRET_KEY="short"), settings_module=PROD)
    assert len(problems) == 1
    assert problems[0].startswith("SECRET_KEY: too short")


@pytest.mark.parametrize("url", ["not-a-url", "mysql://u:p@h/db", "sqlite:///x.db"])
def test_database_url_must_be_postgres(url: str) -> None:
    problems = validate_environment(_env(DATABASE_URL=url), settings_module=DEV)
    assert [p.split(":")[0] for p in problems] == ["DATABASE_URL"]


def test_allowed_hosts_required_in_production_only() -> None:
    assert validate_environment(_env(ALLOWED_HOSTS=None), settings_module=DEV) == []
    problems = validate_environment(_env(ALLOWED_HOSTS=" , "), settings_module=PROD)
    assert [p.split(":")[0] for p in problems] == ["ALLOWED_HOSTS"]


def test_invalid_optional_values_are_named() -> None:
    problems = validate_environment(
        _env(Q_WORKERS="two", DEBUG="maybe", LOG_LEVEL="LOUD", API_PAGE_SIZE=""),
        settings_module=DEV,
    )
    assert sorted(p.split(":")[0] for p in problems) == [
        "API_PAGE_SIZE",
        "DEBUG",
        "LOG_LEVEL",
        "Q_WORKERS",
    ]


def test_retry_must_exceed_timeout() -> None:
    problems = validate_environment(
        _env(Q_TASK_TIMEOUT="600", Q_TASK_RETRY="600"), settings_module=DEV
    )
    assert problems == ["Q_TASK_RETRY: must be greater than Q_TASK_TIMEOUT"]
    # The default retry (360) is also checked against a raised timeout.
    assert validate_environment(_env(Q_TASK_TIMEOUT="400"), settings_module=DEV) != []


def test_check_environment_raises_with_every_variable_named() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        check_environment({"Q_WORKERS": "x"}, settings_module=DEV)
    message = str(excinfo.value)
    for name in ("SECRET_KEY", "DATABASE_URL", "Q_WORKERS", "docs/environment.md"):
        assert name in message


def test_error_message_never_contains_values() -> None:
    secret = "do-not-leak-me"
    environ = _env(
        SECRET_KEY="YOUR_" + secret,
        DATABASE_URL="mysql://user:" + secret + "@h/db",
        Q_WORKERS=secret,
    )
    with pytest.raises(ImproperlyConfigured) as excinfo:
        check_environment(environ, settings_module=DEV)
    assert secret not in str(excinfo.value)


def test_check_environment_passes_silently_when_valid() -> None:
    check_environment(_env(), settings_module=PROD)


def test_settings_load_fails_without_required_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: importing dev settings with an empty environment refuses to start."""
    import importlib
    import sys

    for name in ("SECRET_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", DEV)
    monkeypatch.setattr("environ.Env.read_env", staticmethod(lambda *a, **k: None))
    for module in ("config.settings.base", DEV):
        monkeypatch.delitem(sys.modules, module, raising=False)
    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        importlib.import_module(DEV)
    # Do not leave a half-imported module behind for other tests.
    sys.modules.pop("config.settings.base", None)
