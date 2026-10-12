"""Structured LLM output plumbing (issue #81, ADR 0003). Registers nothing on import.

* ``pydantic_schema(Model)``: a Pydantic model as a ``StructuredSchema``;
* ``structured_call(adapter, prompt, schema)``: validate, one bounded repair, summed usage;
* ``wrap_untrusted`` / ``assert_grounded``: prompt-injection hygiene and evidence checks.

See ``docs/integrations/llm.md``.
"""

from .call import REPAIRED_WARNING, repair_prompt, structured_call, sum_usage
from .errors import OutputIssue, StructuredOutputError, UngroundedOutput
from .safety import (
    UNTRUSTED_SYSTEM_NOTE,
    assert_grounded,
    find_ungrounded,
    normalize_for_match,
    wrap_untrusted,
)
from .schema import PydanticSchema, pydantic_schema

__all__ = [
    "REPAIRED_WARNING",
    "UNTRUSTED_SYSTEM_NOTE",
    "OutputIssue",
    "PydanticSchema",
    "StructuredOutputError",
    "UngroundedOutput",
    "assert_grounded",
    "find_ungrounded",
    "normalize_for_match",
    "pydantic_schema",
    "repair_prompt",
    "structured_call",
    "sum_usage",
    "wrap_untrusted",
]
