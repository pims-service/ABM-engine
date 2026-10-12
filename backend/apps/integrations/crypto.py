"""Authenticated encryption for integration credentials (issue #76).

Credentials are encrypted with Fernet (AES-128-CBC plus HMAC-SHA256, from ``cryptography``). The
keys come from ``settings.INTEGRATION_ENCRYPTION_KEYS`` (env ``INTEGRATION_ENCRYPTION_KEYS``,
comma-separated, **newest first**): new data is always encrypted with the first key, and any key
in the list can decrypt, which is what makes rotation possible without downtime:

1. Prepend a new key, deploy.
2. ``python manage.py rotate_credentials`` re-encrypts every row with the new key.
3. Drop the old key from the list, deploy.

Rules enforced here (full guide: ``backend/README.md``, "Integration credentials"):

* Plaintext lives only inside :class:`Credentials`, whose ``repr``/``str``/``format`` never show
  it, which cannot be pickled (so it never reaches a cache or the task queue) or iterated.
  Reading the value takes an explicit ``reveal()``.
* Failing closed: a wrong or missing key raises :class:`DecryptionError` or
  :class:`EncryptionConfigError`. There is never a plaintext fallback, and messages never carry
  secret material.
* ``mask()`` is the only way to show a hint of a secret.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterable, Mapping
from typing import Any, NoReturn, Self

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

#: What every textual rendering of a secret shows.
REDACTED_TEXT = "[redacted]"
#: Shortest secret that may show its last characters. Below it the hint is fully hidden.
MIN_MASKABLE_LENGTH = 16
MASK_VISIBLE_CHARS = 4
MASK_PREFIX = "****"
#: A Fernet token always starts with the version byte 0x80, which base64 turns into ``gAAAA``.
TOKEN_PREFIX = "gAAAA"  # noqa: S105 - a format marker, not a secret


class EncryptionConfigError(RuntimeError):
    """``INTEGRATION_ENCRYPTION_KEYS`` is missing or holds an invalid key (fail closed)."""


class DecryptionError(RuntimeError):
    """Stored credentials cannot be decrypted with the configured keys (fail closed).

    Usually the key they were encrypted with was removed from ``INTEGRATION_ENCRYPTION_KEYS``,
    or the stored value was altered.
    """


class CredentialsNotSetError(LookupError):
    """The connection has no credentials (or the requested name is not among them)."""


SAFE_DECRYPT_MESSAGE = (
    "Stored credentials could not be decrypted with the configured INTEGRATION_ENCRYPTION_KEYS. "
    "Restore the key they were encrypted with, or set the credentials again."
)


# ------------------------------------------------------------------ keys


def _configured_keys() -> list[str]:
    raw: Any = getattr(settings, "INTEGRATION_ENCRYPTION_KEYS", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(key).strip() for key in raw if str(key).strip()]


def parse_keys(raw: Iterable[str]) -> list[Fernet]:
    """Fernet objects for ``raw`` keys. The error names the position, never the value."""
    fernets: list[Fernet] = []
    for index, key in enumerate(raw, start=1):
        try:
            fernets.append(Fernet(key.strip().encode("ascii")))
        except (ValueError, TypeError, UnicodeEncodeError):
            raise EncryptionConfigError(
                f"INTEGRATION_ENCRYPTION_KEYS: entry {index} is not a valid Fernet key "
                "(32 url-safe base64 bytes)."
            ) from None
    return fernets


def generate_key() -> str:
    """A new random key, for ``INTEGRATION_ENCRYPTION_KEYS`` (also see docs/environment.md)."""
    return Fernet.generate_key().decode("ascii")


def get_cipher() -> MultiFernet:
    """Encrypts with the first configured key and decrypts with any of them."""
    fernets = parse_keys(_configured_keys())
    if not fernets:
        raise EncryptionConfigError(
            "INTEGRATION_ENCRYPTION_KEYS is not set; integration credentials cannot be "
            "encrypted or decrypted."
        )
    return MultiFernet(fernets)


# ------------------------------------------------------------------ encrypt / decrypt


def encrypt_text(plaintext: str) -> str:
    return get_cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    try:
        return get_cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        raise DecryptionError(SAFE_DECRYPT_MESSAGE) from None


def reencrypt_token(token: str) -> str:
    """The same plaintext under the newest key (used by ``rotate_credentials``)."""
    try:
        return get_cipher().rotate(token.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError):
        raise DecryptionError(SAFE_DECRYPT_MESSAGE) from None


def looks_like_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX)


# ------------------------------------------------------------------ masking


def mask(secret: str) -> str:
    """``****abcd`` for a long secret, ``****`` for a short one (a hint must not narrow a guess)."""
    if len(secret) >= MIN_MASKABLE_LENGTH:
        return f"{MASK_PREFIX}{secret[-MASK_VISIBLE_CHARS:]}"
    return MASK_PREFIX


def mask_all(values: Mapping[str, str]) -> dict[str, str]:
    """``{name: mask(value)}``: safe to store and to return from an API."""
    return {name: mask(value) for name, value in values.items()}


# ------------------------------------------------------------------ the wrapper


class Credentials:
    """A set of named secrets (``{"api_key": "..."}``) that never prints itself.

    Two states: *plain* (built from a dict, about to be saved) and *sealed* (loaded from the
    database, still encrypted). Sealed credentials are decrypted only when ``reveal()`` is
    called, so listing connections never touches a key.
    """

    __slots__ = ("_plain", "_token")

    def __init__(self, values: Mapping[str, str] | None = None, *, _token: str = "") -> None:
        self._plain: dict[str, str] | None = dict(values) if values else None
        self._token = _token

    # -- construction
    @classmethod
    def empty(cls) -> Self:
        return cls()

    @classmethod
    def sealed(cls, token: str) -> Self:
        """Credentials as stored: an opaque Fernet token (or "" for none)."""
        return cls(_token=token)

    # -- state
    @property
    def is_set(self) -> bool:
        return bool(self._plain) or bool(self._token)

    @property
    def is_plain(self) -> bool:
        return self._plain is not None

    def token(self) -> str:
        """The encrypted form, to store. Plain values are encrypted on the way out."""
        if self._plain is not None:
            return encrypt_text(json.dumps(self._plain, sort_keys=True, separators=(",", ":")))
        return self._token

    # -- access (explicit)
    def reveal(self) -> dict[str, str]:
        """A copy of the secrets. Raises ``DecryptionError`` if they cannot be decrypted."""
        if self._plain is not None:
            return dict(self._plain)
        if not self._token:
            return {}
        try:
            data = json.loads(decrypt_text(self._token))
        except ValueError:
            raise DecryptionError(SAFE_DECRYPT_MESSAGE) from None
        if not isinstance(data, dict):
            raise DecryptionError(SAFE_DECRYPT_MESSAGE)
        return {str(k): str(v) for k, v in data.items()}

    def get(self, name: str) -> str:
        """One secret by name. ``CredentialsNotSetError`` if missing (the message names only it)."""
        values = self.reveal()
        if name not in values:
            raise CredentialsNotSetError(f"Credential '{name}' is not set.")
        return values[name]

    def names(self) -> list[str]:
        return sorted(self.reveal())

    # -- never print, copy or serialize by accident
    def __repr__(self) -> str:
        return f"Credentials({REDACTED_TEXT if self.is_set else 'empty'})"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return repr(self)

    def __bool__(self) -> bool:
        return self.is_set

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Credentials):
            return NotImplemented
        if not (self.is_set and other.is_set):
            return self.is_set == other.is_set
        try:
            return hmac.compare_digest(
                json.dumps(self.reveal(), sort_keys=True),
                json.dumps(other.reveal(), sort_keys=True),
            )
        except DecryptionError:
            return False

    __hash__ = None  # type: ignore[assignment]  # mutable-looking and secret: not hashable

    def _refuse(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("Credentials cannot be pickled, copied or serialized.")

    __reduce__ = _refuse
    __reduce_ex__ = _refuse
    __getstate__ = _refuse

    def __copy__(self) -> Self:
        # In-memory copies (Django copies model state) are harmless; only serialization is not.
        clone = type(self)()
        clone._plain = None if self._plain is None else dict(self._plain)
        clone._token = self._token
        return clone

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self.__copy__()
