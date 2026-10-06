"""The log scrubber redacts values of secret-looking fields."""

from __future__ import annotations

import logging
from typing import cast

import pytest
from django.conf import settings

from config.logging_filters import REDACTED, SecretScrubbingFilter


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("login password=hunter2 ok", "hunter2"),
        ("api_key: abc123xyz", "abc123xyz"),
        ('{"token": "tok-value-1", "user": "bob"}', "tok-value-1"),
        ("headers Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("DB_PASSWORD=pw-value", "pw-value"),
        ("connect postgres://user:pw-value@host:5432/db", "pw-value"),
        ("ANTHROPIC_API_KEY='k-value'", "k-value"),
    ],
)
def test_secret_values_are_redacted(raw: str, leaked: str) -> None:
    scrubbed = SecretScrubbingFilter().scrub(raw)
    assert leaked not in scrubbed
    assert REDACTED in scrubbed


def test_non_secret_text_is_untouched() -> None:
    text = "user=bob page=3 status=200"
    assert SecretScrubbingFilter().scrub(text) == text


def test_extra_fields_are_redacted() -> None:
    scrubbed = SecretScrubbingFilter(extra_fields=["ssn"]).scrub("ssn=123-45-6789")
    assert scrubbed == f"ssn={REDACTED}"


def test_blank_extra_fields_are_ignored() -> None:
    text = "user=bob page=3"
    assert SecretScrubbingFilter(extra_fields=["", " "]).scrub(text) == text


def test_filter_scrubs_formatted_record() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "pw %s", ("password=abc",), None)
    assert SecretScrubbingFilter().filter(record) is True
    assert record.getMessage() == f"pw password={REDACTED}"


def test_logging_config_installs_the_scrubber() -> None:
    handlers = cast("dict[str, dict[str, list[str]]]", settings.LOGGING["handlers"])
    assert "scrub_secrets" in handlers["console"]["filters"]
