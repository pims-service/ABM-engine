"""Idempotent import identity (issue #66): one provider company, one stable key.

``provider_company_key("Acme-Data", "12345")`` -> ``"acme-data:12345"``. The key is what makes
re-importing the same provider company detectable before any fuzzy matching (#58, #67):

* The provider name is lower-cased and must match ``[a-z0-9][a-z0-9_.-]{0,63}`` (so it never
  contains ``:``); the provider id is kept exactly as the provider gave it (ids can be case
  sensitive), stripped, 1-200 characters, no control characters.
* Where it is stored: ``DataSource.name`` is the provider name and
  ``DataSource.provider_reference`` is the provider id, so the key is
  ``f"{source.name}:{source.provider_reference}"``. ``ImportRow.raw_data["provider_key"]`` carries
  it too (see ``ProviderCompany.to_raw_data``).
* Rule: two rows with the same key are the same company. A provider company without an id has
  no key and falls back to the normal website/name matching (``None`` from
  ``try_provider_company_key``); a changed id is a different company by this rule.
* The profile URL is never part of the key and is never fetched.
"""

from __future__ import annotations

import re

PROVIDER_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
PROVIDER_ID_MAX_LENGTH = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def provider_company_key(provider_name: str, provider_id: str) -> str:
    """The stable identity key of a provider company. Raises ``ValueError`` on a bad name/id."""
    if not isinstance(provider_name, str) or not isinstance(provider_id, str):
        raise ValueError("provider name and id must be text")
    name = provider_name.strip().lower()
    pid = provider_id.strip()
    if not PROVIDER_NAME_PATTERN.fullmatch(name):
        raise ValueError("invalid provider name")
    if not pid or len(pid) > PROVIDER_ID_MAX_LENGTH or _CONTROL.search(pid):
        raise ValueError("invalid provider id")
    return f"{name}:{pid}"


def try_provider_company_key(provider_name: object, provider_id: object) -> str | None:
    """Like ``provider_company_key`` but ``None`` (no key) instead of raising."""
    if not isinstance(provider_name, str) or not isinstance(provider_id, str):
        return None
    try:
        return provider_company_key(provider_name, provider_id)
    except ValueError:
        return None


def split_provider_company_key(key: str) -> tuple[str, str]:
    """``(provider_name, provider_id)`` of a key made by ``provider_company_key``."""
    name, sep, pid = key.partition(":")
    if not sep or provider_company_key(name, pid) != key:
        raise ValueError("not a provider company key")
    return name, pid
