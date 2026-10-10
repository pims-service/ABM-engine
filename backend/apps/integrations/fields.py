"""``EncryptedCredentialsField``: a column that only ever holds ciphertext (issue #76).

Why a field and not service-level encryption: the ORM, fixtures, ``values()``, ``dumpdata`` and
every save path see only the encrypted token, so no code path (admin, serializer, bulk update)
can write plaintext to the database by forgetting a call. The Python value is a
:class:`~apps.integrations.crypto.Credentials` wrapper that does not print itself.

* Stored as ``text``: a Fernet token, or ``""`` for "no credentials".
* Loading never decrypts (``Credentials.sealed``); ``reveal()`` does, on demand and fail closed.
* Saving a loaded, unchanged value writes the same token back, so renaming a connection needs no
  key. Saving a new value encrypts with the newest key.
* The column cannot be filtered on (ciphertext is random) and refuses plain strings.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core import exceptions
from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper

from .crypto import Credentials, looks_like_token


class EncryptedCredentialsField(models.TextField):  # type: ignore[type-arg]
    description = "Encrypted credentials (Fernet token)"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("blank", True)
        kwargs.pop("default", None)  # the default is always "no credentials"
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("blank", None)
        return name, path, args, kwargs

    def get_default(self) -> Credentials:
        return Credentials.empty()

    def from_db_value(self, value: Any, expression: Any, connection: BaseDatabaseWrapper) -> Any:
        return Credentials.sealed(value or "")

    def to_python(self, value: Any) -> Credentials:
        if isinstance(value, Credentials):
            return value
        if value is None or value == "":
            return Credentials.empty()
        if isinstance(value, str) and looks_like_token(value):
            return Credentials.sealed(value)  # e.g. loaddata: a token produced by value_to_string
        raise exceptions.ValidationError(
            "Credentials must be set through the integrations service.", code="invalid"
        )

    def get_prep_value(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, Credentials):
            raise TypeError("EncryptedCredentialsField only accepts Credentials objects.")
        return value.token()

    def value_to_string(self, obj: models.Model) -> str:
        return self.get_prep_value(self.value_from_object(obj))

    def formfield(self, *args: Any, **kwargs: Any) -> None:
        return None  # never offered in a ModelForm (the admin must not edit or show it)
