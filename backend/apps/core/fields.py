"""Custom model fields shared by several apps."""

from __future__ import annotations

import json
from typing import Any

from django.core import checks, exceptions
from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper


class StringListField(models.Field):  # type: ignore[type-arg]  # not subscriptable at runtime
    """An ordered list of short strings.

    On PostgreSQL this is a native ``text[]`` column (data model ground rules). Elsewhere (the
    SQLite test path) it is a ``text`` column holding a JSON array, so the same model code and
    migrations work on both. Order and duplicates are preserved exactly as given; normalise in
    the service layer if you need sets.

    Defaults to an empty list (``default=list`` is set for you) and never stores NULL.
    """

    description = "List of strings"
    empty_strings_allowed = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("default", list)
        kwargs.setdefault("blank", True)  # an empty list is a valid value
        super().__init__(*args, **kwargs)

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        errors = super().check(**kwargs)
        if self.null:
            errors.append(
                checks.Error(
                    "StringListField must not be null; use an empty list.",
                    obj=self,
                    id="core.E001",
                )
            )
        return errors

    def get_internal_type(self) -> str:
        return "StringListField"

    def db_type(self, connection: BaseDatabaseWrapper) -> str:
        return "text[]" if connection.vendor == "postgresql" else "text"

    def to_python(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise exceptions.ValidationError("Enter a valid list.", code="invalid") from exc
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise exceptions.ValidationError("Enter a list of strings.", code="invalid")
        return list(value)

    def from_db_value(self, value: Any, expression: Any, connection: BaseDatabaseWrapper) -> Any:
        if value is None or isinstance(value, list):
            return value
        return json.loads(value)

    def get_db_prep_value(
        self, value: Any, connection: BaseDatabaseWrapper, prepared: bool = False
    ) -> Any:
        if value is None:
            return None
        items = self.to_python(value)
        return items if connection.vendor == "postgresql" else json.dumps(items)

    def value_to_string(self, obj: models.Model) -> str:
        return json.dumps(self.value_from_object(obj))
