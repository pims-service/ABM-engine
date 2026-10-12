"""``structured_call``: the one entry point for AI features (issue #81, ADR 0003).

    result = structured_call(adapter, prompt, pydantic_schema(Model))   # AdapterResult[Model]

* Runs ``adapter.generate_structured`` through ``execute`` (declared-feature check, typed errors).
* If the output does not validate, makes at most ONE repair attempt (``max_repairs`` is 0 or 1;
  there is no loop). The repair prompt is the original prompt plus the field PATHS and codes that
  failed; the rejected model text is never fed back, so injected or personal text cannot be
  replayed into the next call. A second failure raises ``StructuredOutputError``.
* Usage (tokens, cost, latency) is summed over every attempt, failed ones included.
* The prompt name and version are in ``result.provenance.source_ref`` (``prompt:<name>@<version>``)
  and in the log line (names, versions, counts; never prompt text or output).
* Errors other than invalid output (auth, rate limit, timeout ...) are not repaired and
  propagate unchanged; retrying them is the job layer's decision (#78).
"""

from __future__ import annotations

import dataclasses
import logging
from decimal import Decimal

from apps.integrations.framework import (
    AdapterResult,
    LLMAdapter,
    PromptSpec,
    ProviderInvalidOutput,
    StructuredSchema,
    Usage,
    execute,
)

from .errors import OutputIssue, StructuredOutputError

logger = logging.getLogger(__name__)

REPAIRED_WARNING = "output repaired after one invalid attempt"


def sum_usage(first: Usage, second: Usage) -> Usage:
    """Add two ``Usage`` values; a part unknown in both stays unknown. The model is the later
    one's."""

    def add_int(a: int | None, b: int | None) -> int | None:
        return None if a is None and b is None else (a or 0) + (b or 0)

    def add_dec(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        return None if a is None and b is None else (a or Decimal(0)) + (b or Decimal(0))

    return Usage(
        credits=add_dec(first.credits, second.credits),
        cost=add_dec(first.cost, second.cost),
        currency=second.currency or first.currency,
        input_tokens=add_int(first.input_tokens, second.input_tokens),
        output_tokens=add_int(first.output_tokens, second.output_tokens),
        model=second.model or first.model,
        latency_ms=add_int(first.latency_ms, second.latency_ms),
    )


def repair_prompt(prompt: PromptSpec, issues: tuple[OutputIssue, ...]) -> PromptSpec:
    """``prompt`` plus a correction note naming the failed fields (paths and codes only)."""
    if issues:
        problems = "; ".join(str(i) for i in issues)
        note = f"Problems found: {problems}."
    else:
        note = "The answer did not match the required structure."
    feedback = (
        "\n\nYour previous answer was rejected by validation. "
        f"{note} Answer again with the complete JSON object that matches the required schema, "
        "using only information from the task."
    )
    return dataclasses.replace(prompt, user=prompt.user + feedback)


def _log(adapter: LLMAdapter, prompt: PromptSpec, attempt: int, usage: Usage, outcome: str) -> None:
    logger.info(
        "llm_call",
        extra={
            "provider": adapter.key,
            "prompt_name": prompt.name,
            "prompt_version": prompt.version,
            "attempt": attempt,
            "outcome": outcome,
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        },
    )


def structured_call[T](
    adapter: LLMAdapter,
    prompt: PromptSpec,
    schema: StructuredSchema[T],
    *,
    max_repairs: int = 1,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> AdapterResult[T]:
    """Run ``prompt`` and return the validated object. ``temperature`` defaults to 0 (use it for
    classification and extraction); ``max_tokens`` caps the answer. See the module docstring."""
    if max_repairs not in (0, 1):
        raise ValueError("max_repairs must be 0 or 1: at most one repair attempt")
    current = prompt
    total = Usage()
    for attempt in range(1, max_repairs + 2):
        try:
            result = execute(
                adapter,
                adapter.generate_structured,
                current,
                schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ProviderInvalidOutput as exc:
            usage = getattr(exc, "usage", None)
            total = sum_usage(total, usage) if isinstance(usage, Usage) else total
            _log(adapter, prompt, attempt, total, "invalid_output")
            issues = tuple(getattr(exc, "issues", ()))
            if attempt > max_repairs:
                raise StructuredOutputError(
                    issues, provider_name=adapter.key, usage=total, attempts=attempt
                ) from None
            current = repair_prompt(prompt, issues)
            continue
        total = sum_usage(total, result.usage)
        _log(adapter, prompt, attempt, total, "ok" if attempt == 1 else "repaired")
        warnings = result.warnings if attempt == 1 else (*result.warnings, REPAIRED_WARNING)
        return dataclasses.replace(result, usage=total, warnings=warnings)
    raise AssertionError("unreachable")  # pragma: no cover
