"""Errors of the structured-output layer (issue #81).

``StructuredOutputError`` is a ``ProviderInvalidOutput`` (same ``code``) that also carries the
list of problems as field PATHS and validator CODES, never model text, so an error report, a
log line and the one repair prompt can show what was wrong without echoing model output (which
may contain scraped text or personal data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.integrations.framework import ProviderInvalidOutput, Usage

MAX_ISSUES = 20
_SAFE_PATH = re.compile(r"[^A-Za-z0-9_.\[\]-]")
_SAFE_CODE = re.compile(r"[^a-z0-9_.]")


@dataclass(frozen=True)
class OutputIssue:
    """One validation problem: where (``path``, dotted, ``""`` for the whole object) and what
    kind (``code``, a validator identifier such as ``missing``). Both are sanitized."""

    path: str
    code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _SAFE_PATH.sub("?", str(self.path))[:60])
        object.__setattr__(self, "code", _SAFE_CODE.sub("_", str(self.code).lower())[:40])

    def __str__(self) -> str:
        return f"{self.path or '(root)'} ({self.code})"


class StructuredOutputError(ProviderInvalidOutput):
    """Model output that does not validate. ``issues`` are paths and codes only; ``usage`` is
    what the failed attempt(s) cost (so budgets stay accurate); ``attempts`` how many calls were
    made."""

    def __init__(
        self,
        issues: tuple[OutputIssue, ...] | list[OutputIssue] = (),
        *,
        provider_name: str = "",
        usage: Usage | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(provider_name=provider_name)
        self.issues: tuple[OutputIssue, ...] = tuple(issues)[:MAX_ISSUES]
        self.usage: Usage = usage or Usage()
        self.attempts = attempts
        if self.issues:
            self.args = (f"{self.message} Problems: {'; '.join(str(i) for i in self.issues)}.",)

    def as_dict(self) -> dict[str, object]:
        return {
            **super().as_dict(),
            "issues": [{"path": i.path, "code": i.code} for i in self.issues],
            "attempts": self.attempts,
        }


class UngroundedOutput(ProviderInvalidOutput):
    """Output values that do not appear in the evidence (``assert_grounded``). ``fields`` names
    the output fields only, never their values."""

    message = "The model output contains claims that are not in the supplied evidence."

    def __init__(
        self, fields: tuple[str, ...] | list[str] = (), *, provider_name: str = ""
    ) -> None:
        super().__init__(provider_name=provider_name)
        self.fields: tuple[str, ...] = tuple(_SAFE_PATH.sub("?", str(f))[:60] for f in fields)[
            :MAX_ISSUES
        ]
        if self.fields:
            self.args = (f"{self.message} Fields: {', '.join(self.fields)}.",)

    def as_dict(self) -> dict[str, object]:
        return {**super().as_dict(), "fields": list(self.fields)}
