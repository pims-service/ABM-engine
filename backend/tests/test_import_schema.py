"""The shared company input schema and the raw-data scrubbing rule (issue #56)."""

from __future__ import annotations

import json

import pytest

from apps.imports import schema
from apps.imports.rawdata import MAX_RAW_BYTES, MAX_VALUE_LENGTH, sanitize_raw_data
from apps.imports.schema import CompanyInput, InputErrors, validate_company_input


def _codes(result: object) -> list[str]:
    assert isinstance(result, InputErrors)
    return result.codes


def test_minimal_input_is_valid() -> None:
    result = validate_company_input({"name": "  Acme   Corp "})
    assert result == CompanyInput(name="Acme Corp")
    assert result.domain is None


def test_full_input_is_normalized() -> None:
    result = validate_company_input(
        {
            "name": "Acme",
            "website": " https://www.Acme.com/about ",
            "profile_url": "https://www.linkedin.com/company/acme",
            "country": "sa",
            "ignored": "x",
        }
    )
    assert isinstance(result, CompanyInput)
    assert result.website == "https://www.Acme.com/about"
    assert result.domain == "acme.com"
    assert result.country == "SA"
    assert result.as_dict()["domain"] == "acme.com"


def test_bare_domain_is_accepted_and_none_values_are_blank() -> None:
    result = validate_company_input({"name": "A", "website": "acme.com/x", "country": None})
    assert isinstance(result, CompanyInput)
    assert result.domain == "acme.com"
    assert result.country == ""


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({}, schema.NAME_REQUIRED),
        ({"name": "   "}, schema.NAME_REQUIRED),
        ({"name": "x" * 301}, schema.NAME_TOO_LONG),
        ({"name": 5}, schema.NOT_TEXT),
        ({"name": "a\x00b"}, schema.INVALID_CHARACTERS),
        ({"name": "A", "website": "ftp://acme.com"}, schema.WEBSITE_INVALID_SCHEME),
        ({"name": "A", "website": "javascript:alert(1)"}, schema.WEBSITE_INVALID),
        ({"name": "A", "website": "localhost"}, schema.WEBSITE_INVALID),
        ({"name": "A", "website": "https://u:p@acme.com"}, schema.WEBSITE_HAS_CREDENTIALS),
        ({"name": "A", "website": "a.com/" + "x" * 2000}, schema.WEBSITE_TOO_LONG),
        ({"name": "A", "profile_url": "acme"}, schema.PROFILE_URL_INVALID),
        ({"name": "A", "profile_url": "ftp://x.com/a"}, schema.PROFILE_URL_INVALID_SCHEME),
        ({"name": "A", "profile_url": "https://u:p@x.com/a"}, schema.PROFILE_URL_HAS_CREDENTIALS),
        ({"name": "A", "profile_url": "https://" + "x" * 2000}, schema.PROFILE_URL_TOO_LONG),
        ({"name": "A", "profile_url": "https://not a url"}, schema.PROFILE_URL_INVALID),
        ({"name": "A", "country": "SAU"}, schema.COUNTRY_INVALID_FORMAT),
        ({"name": "A", "country": "1A"}, schema.COUNTRY_INVALID_FORMAT),
        ({"name": "A", "country": "ZZ"}, schema.COUNTRY_UNKNOWN),
    ],
)
def test_field_errors_have_stable_codes(raw: dict[str, object], code: str) -> None:
    result = validate_company_input(raw)
    assert code in _codes(result)
    assert all(c in schema.ERROR_CODES for c in _codes(result))


def test_errors_are_field_level_and_collected() -> None:
    result = validate_company_input({"name": "", "website": "nope", "country": "ZZ"})
    assert isinstance(result, InputErrors)
    assert set(result.as_dict()) == {"name", "website", "country"}
    assert result.first_code == schema.NAME_REQUIRED
    assert "name:" in result.message()
    assert result


def test_non_mapping_is_a_row_error() -> None:
    result = validate_company_input(["Acme"])
    assert isinstance(result, InputErrors)
    assert result.errors[0].field == schema.ROW_FIELD
    assert result.errors[0].as_dict()["code"] == schema.INVALID_INPUT
    assert result.message() == schema.ERROR_MESSAGES[schema.INVALID_INPUT]


def test_require_identifier() -> None:
    assert _codes(validate_company_input({"name": "A"}, require_identifier=True)) == [
        schema.IDENTIFIER_REQUIRED
    ]
    ok = validate_company_input(
        {"name": "A", "profile_url": "https://x.com/a"}, require_identifier=True
    )
    assert isinstance(ok, CompanyInput)
    # A broken website reports its own error, not a second "required" one.
    assert _codes(
        validate_company_input({"name": "A", "website": "x"}, require_identifier=True)
    ) == [schema.WEBSITE_INVALID]


# ------------------------------------------------------------------ raw data


def test_sensitive_keys_and_embedded_secrets_are_redacted() -> None:
    raw = {
        "name": "Acme",
        "api_key": "abc123",  # pragma: allowlist secret
        "Password": "hunter2",  # pragma: allowlist secret
        "note": "call me, token=abc123xyz",  # pragma: allowlist secret
        "nested": {"authorization": "Bearer abcdefghijklmnop", "ok": 1},
        "url": "https://user:pw@example.com/x",  # pragma: allowlist secret
    }
    stored = json.dumps(sanitize_raw_data(raw))
    for leaked in ("abc123", "hunter2", "abcdefghijklmnop", ":pw@"):
        assert leaked not in stored
    assert sanitize_raw_data(raw)["name"] == "Acme"
    assert sanitize_raw_data(raw)["nested"]["ok"] == 1


def test_sanitize_is_idempotent_and_handles_odd_input() -> None:
    assert sanitize_raw_data(None) == {}
    assert sanitize_raw_data("plain") == {"value": "plain"}
    once = sanitize_raw_data({"a": {1, 2}, "b": object(), "c": (1, "x")})
    assert sanitize_raw_data(once) == once
    json.dumps(once)


def test_values_and_total_size_are_capped() -> None:
    long = sanitize_raw_data({"a": "x" * (MAX_VALUE_LENGTH + 500)})
    assert len(long["a"]) == MAX_VALUE_LENGTH
    big = sanitize_raw_data({f"k{i}": "y" * MAX_VALUE_LENGTH for i in range(50)})
    assert big["_truncated"] is True
    assert len(json.dumps(big).encode()) <= MAX_RAW_BYTES
    assert sanitize_raw_data(big) == big
    deep: dict[str, object] = {"a": 1}
    for _ in range(10):
        deep = {"n": deep}
    json.dumps(sanitize_raw_data(deep))
