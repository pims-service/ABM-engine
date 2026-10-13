"""Pydantic models as structured-output schemas (issue #81, ADR 0003, Brief section 18).

``pydantic_schema(Model)`` returns a ``StructuredSchema`` whose ``json_schema`` comes from
``Model.model_json_schema()`` and whose ``parse`` validates decoded JSON with
``Model.model_validate``. A failure raises ``StructuredOutputError`` listing field paths and
validator codes only; the offending values (model text, possibly personal data) are never in
the message.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ValidationError

from apps.integrations.framework import StructuredSchema

from .errors import MAX_ISSUES, OutputIssue, StructuredOutputError

_NAME = re.compile(r"[^A-Za-z0-9_-]")


def issues_from_validation_error(exc: ValidationError) -> tuple[OutputIssue, ...]:
    """Paths and codes of a Pydantic ``ValidationError``; messages, inputs and context dropped."""
    out: list[OutputIssue] = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        path = ".".join(str(part) for part in err.get("loc", ()))
        out.append(OutputIssue(path, str(err.get("type", "invalid"))))
        if len(out) >= MAX_ISSUES:
            break
    return tuple(out)


class PydanticSchema[M: BaseModel](StructuredSchema[M]):
    """A ``StructuredSchema`` whose failures carry field paths and codes."""

    def parse(self, raw: Any, *, provider_name: str = "") -> M:
        try:
            return self.validate(raw)
        except ValidationError as exc:
            raise StructuredOutputError(
                issues_from_validation_error(exc), provider_name=provider_name
            ) from None
        except Exception:
            raise StructuredOutputError(provider_name=provider_name) from None


def pydantic_schema[M: BaseModel](model: type[M], *, name: str | None = None) -> PydanticSchema[M]:
    """The schema for ``model``. ``name`` defaults to the class name."""
    schema_name = _NAME.sub("_", name or model.__name__)[:64] or "output"
    return PydanticSchema(schema_name, model.model_json_schema(), model.model_validate)
