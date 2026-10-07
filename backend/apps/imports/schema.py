"""The one company input shape every channel shares (issue #56): manual entry, CSV rows and
provider imports all hand ``validate_company_input`` a mapping and get back the same thing.

    result = validate_company_input({"name": "Acme", "website": "acme.com", "country": "sa"})
    if isinstance(result, InputErrors):
        result.as_dict()      # {"website": [{"code": "...", "message": "..."}]}
    else:
        result                 # CompanyInput(name="Acme", website="acme.com", domain="acme.com",
                               #              profile_url="", country="SA")

Accepted keys: ``name`` (required), ``website``, ``profile_url`` and ``country`` (optional).
Other keys are ignored here (they stay in ``ImportRow.raw_data``); mapping a CSV header or a
provider field onto these four names is the caller's job.

Rules (each error has a stable ``code`` that clients and the error report may rely on):

* ``name``: required, at most 300 characters, whitespace collapsed.
* ``website``: at most 2000 characters. A bare ``acme.com/about`` is fine, a scheme must be
  http or https, credentials in the URL are refused, and the host must be a real domain name
  (``apps.companies.domain.normalize_domain`` is the judge). Kept as entered; ``domain`` is the
  normalized host (what dedupe compares).
* ``profile_url``: at most 2000 characters, an absolute http(s) URL with a host, no credentials.
* ``country``: an ISO 3166-1 alpha-2 code (any case in, upper case out).
* ``require_identifier=True`` (the manual entry rule of Brief section 4A) also requires a
  website or a profile URL. Imports leave it off: a name alone is still a company to review.

Control characters are refused everywhere. The function never raises for bad data and never
looks at the database.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.campaigns.reference import ISO_3166_ALPHA2
from apps.companies.domain import normalize_domain

NAME_MAX_LENGTH = 300
URL_MAX_LENGTH = 2000
INPUT_FIELDS = ("name", "website", "profile_url", "country")

# Stable error codes.
NAME_REQUIRED = "name_required"
NAME_TOO_LONG = "name_too_long"
WEBSITE_TOO_LONG = "website_too_long"
WEBSITE_INVALID = "website_invalid"
WEBSITE_INVALID_SCHEME = "website_invalid_scheme"
WEBSITE_HAS_CREDENTIALS = "website_has_credentials"
PROFILE_URL_TOO_LONG = "profile_url_too_long"
PROFILE_URL_INVALID = "profile_url_invalid"
PROFILE_URL_INVALID_SCHEME = "profile_url_invalid_scheme"
PROFILE_URL_HAS_CREDENTIALS = "profile_url_has_credentials"
COUNTRY_INVALID_FORMAT = "country_invalid_format"
COUNTRY_UNKNOWN = "country_unknown"
IDENTIFIER_REQUIRED = "identifier_required"
NOT_TEXT = "not_text"
INVALID_CHARACTERS = "invalid_characters"
INVALID_INPUT = "invalid_input"

#: Row-level problems have no single field; they are reported under this key.
ROW_FIELD = "__row__"

ERROR_MESSAGES: dict[str, str] = {
    NAME_REQUIRED: "Enter the company name.",
    NAME_TOO_LONG: f"The name must be at most {NAME_MAX_LENGTH} characters.",
    WEBSITE_TOO_LONG: f"The website must be at most {URL_MAX_LENGTH} characters.",
    WEBSITE_INVALID: "Enter a website with a valid domain name, such as example.com.",
    WEBSITE_INVALID_SCHEME: "The website must start with http:// or https://, or have no scheme.",
    WEBSITE_HAS_CREDENTIALS: "The website must not contain a username or password.",
    PROFILE_URL_TOO_LONG: f"The profile URL must be at most {URL_MAX_LENGTH} characters.",
    PROFILE_URL_INVALID: "Enter a full profile URL, such as https://www.linkedin.com/company/acme.",
    PROFILE_URL_INVALID_SCHEME: "The profile URL must start with http:// or https://.",
    PROFILE_URL_HAS_CREDENTIALS: "The profile URL must not contain a username or password.",
    COUNTRY_INVALID_FORMAT: "Use a two-letter ISO country code, such as SA.",
    COUNTRY_UNKNOWN: "This is not an assigned ISO 3166-1 country code.",
    IDENTIFIER_REQUIRED: "Enter a website or a profile URL.",
    NOT_TEXT: "Send this value as text.",
    INVALID_CHARACTERS: "Remove control characters from this value.",
    INVALID_INPUT: "Each company must be an object of named values.",
}

#: Every code this module can return (the contract; a test pins it).
ERROR_CODES = frozenset(ERROR_MESSAGES)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_VALIDATOR = URLValidator(schemes=["http", "https"])


@dataclass(frozen=True)
class FieldError:
    """One problem with one field. ``code`` is stable; ``message`` is for people."""

    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class CompanyInput:
    """A valid, normalized company input. ``domain`` is derived from ``website`` (or ``None``).

    The fields are the ones ``apps.companies.services.create_company`` takes.
    """

    name: str
    website: str = ""
    profile_url: str = ""
    country: str = ""
    domain: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "website": self.website,
            "domain": self.domain,
            "profile_url": self.profile_url,
            "country": self.country,
        }


@dataclass(frozen=True)
class InputErrors:
    """What ``validate_company_input`` returns for invalid input: every problem found."""

    errors: tuple[FieldError, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:  # an error result is "something wrong"
        return bool(self.errors)

    @property
    def codes(self) -> list[str]:
        return [e.code for e in self.errors]

    @property
    def first_code(self) -> str:
        return self.errors[0].code if self.errors else ""

    def as_dict(self) -> dict[str, list[dict[str, str]]]:
        """``{field: [{"code", "message"}, ...]}``, the shape of a field-level API error."""
        out: dict[str, list[dict[str, str]]] = {}
        for error in self.errors:
            out.setdefault(error.field, []).append({"code": error.code, "message": error.message})
        return out

    def message(self) -> str:
        """All messages in one line, for an ``ImportRow.error_message``."""
        return " ".join(
            f"{e.field}: {e.message}" if e.field != ROW_FIELD else e.message for e in self.errors
        )


def _error(field_name: str, code: str) -> FieldError:
    return FieldError(field_name, code, ERROR_MESSAGES[code])


def _text(raw: Mapping[str, Any], key: str, errors: list[FieldError]) -> str:
    """The stripped text of ``raw[key]``; ``None`` and a missing key are blank."""
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.append(_error(key, NOT_TEXT))
        return ""
    if _CONTROL_CHARS.search(value):
        errors.append(_error(key, INVALID_CHARACTERS))
        return ""
    return value.strip()


def _has_credentials(url: str) -> bool:
    try:
        parts = urlsplit(url if "://" in url else "//" + url)
        return parts.username is not None or parts.password is not None
    except ValueError:
        return False


def _check_website(website: str) -> FieldError | None:
    if len(website) > URL_MAX_LENGTH:
        return _error("website", WEBSITE_TOO_LONG)
    if "://" in website and website.split("://", 1)[0].lower() not in ("http", "https"):
        return _error("website", WEBSITE_INVALID_SCHEME)
    if _has_credentials(website):
        return _error("website", WEBSITE_HAS_CREDENTIALS)
    if normalize_domain(website) is None:
        return _error("website", WEBSITE_INVALID)
    return None


def _check_profile_url(url: str) -> FieldError | None:
    if len(url) > URL_MAX_LENGTH:
        return _error("profile_url", PROFILE_URL_TOO_LONG)
    scheme = urlsplit(url).scheme.lower() if ":" in url else ""
    if scheme and scheme not in ("http", "https"):
        return _error("profile_url", PROFILE_URL_INVALID_SCHEME)
    if not scheme:
        return _error("profile_url", PROFILE_URL_INVALID)
    if _has_credentials(url):
        return _error("profile_url", PROFILE_URL_HAS_CREDENTIALS)
    try:
        _URL_VALIDATOR(url)
    except ValidationError:
        return _error("profile_url", PROFILE_URL_INVALID)
    return None


def validate_company_input(
    raw: Mapping[str, Any] | Any, *, require_identifier: bool = False
) -> CompanyInput | InputErrors:
    """Validate and normalize one company. Returns a ``CompanyInput``, or an ``InputErrors``
    holding every problem found (field-level, stable codes). Never raises for bad data."""
    if not isinstance(raw, Mapping):
        return InputErrors((_error(ROW_FIELD, INVALID_INPUT),))

    errors: list[FieldError] = []
    name = " ".join(_text(raw, "name", errors).split())
    website = _text(raw, "website", errors)
    profile_url = _text(raw, "profile_url", errors)
    country = _text(raw, "country", errors).upper()
    failed = {e.field for e in errors}

    if "name" not in failed:
        if not name:
            errors.append(_error("name", NAME_REQUIRED))
        elif len(name) > NAME_MAX_LENGTH:
            errors.append(_error("name", NAME_TOO_LONG))

    if website and "website" not in failed and (problem := _check_website(website)):
        errors.append(problem)
    if profile_url and "profile_url" not in failed and (problem := _check_profile_url(profile_url)):
        errors.append(problem)

    if country and "country" not in failed:
        if not (len(country) == 2 and country.isascii() and country.isalpha()):
            errors.append(_error("country", COUNTRY_INVALID_FORMAT))
        elif country not in ISO_3166_ALPHA2:
            errors.append(_error("country", COUNTRY_UNKNOWN))

    if (
        require_identifier
        and not website
        and not profile_url
        and not failed
        & {
            "website",
            "profile_url",
        }
    ):
        errors.append(_error("website", IDENTIFIER_REQUIRED))

    if errors:
        return InputErrors(tuple(errors))
    return CompanyInput(
        name=name,
        website=website,
        profile_url=profile_url,
        country=country,
        domain=normalize_domain(website),
    )
