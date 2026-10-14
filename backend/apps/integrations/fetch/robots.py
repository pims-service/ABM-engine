"""A small, safe ``robots.txt`` parser following RFC 9309 (issue #94).

``urllib.robotparser`` is not used because it ignores the ``*`` and ``$`` wildcards, so a rule like
``Disallow: /*.pdf$`` or ``Disallow: /private/*`` would silently allow what the site disallowed.

Rules implemented:

* groups of ``User-agent`` lines; the group whose token equals our product token (``ABMEngine``,
  case-insensitive) wins over the ``*`` group; groups with the same token are merged;
* ``Allow`` and ``Disallow`` with ``*`` (any run of characters) and ``$`` (end of URL);
  the longest matching pattern wins and ``Allow`` wins a tie; an empty ``Disallow`` allows all;
* ``Crawl-delay`` (seconds, may be fractional) of the selected group, a widely used extension;
* matching is on the path plus query, after percent-encoding normalisation;
* limits: 1,000 rules, 512 characters per line, so a hostile file cannot cost real time. Matching
  is linear in ``len(path) * number of stars`` (no regular expressions, no backtracking).

The status handling of RFC 9309 lives in ``fetcher.py``: a 4xx answer means no rules (allow all),
a 5xx or an unreachable server means disallow all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

MAX_RULES = 1000
MAX_LINE = 512
MAX_DELAY = 3600.0

_NORMAL_SAFE = "/*$?=&:@,;+!~'()-._"
_DELAY = re.compile(r"\d{1,5}(\.\d{1,3})?")


def _normalise(value: str) -> str:
    return quote(unquote(value), safe=_NORMAL_SAFE)


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str  # normalised, without the trailing "$"
    anchored: bool  # pattern ended with "$"

    @property
    def specificity(self) -> int:
        return len(self.pattern) + (1 if self.anchored else 0)

    def matches(self, path: str) -> bool:
        segments = self.pattern.split("*")
        if not path.startswith(segments[0]):
            return False
        pos = len(segments[0])
        if len(segments) == 1:
            return pos == len(path) if self.anchored else True
        for segment in segments[1:-1]:
            index = path.find(segment, pos)
            if index < 0:
                return False
            pos = index + len(segment)
        last = segments[-1]
        if self.anchored:
            return len(path) - len(last) >= pos and path.endswith(last)
        return path.find(last, pos) >= 0


@dataclass(frozen=True)
class RobotsRules:
    """The rules that apply to us. ``RobotsRules()`` allows everything."""

    rules: tuple[Rule, ...] = ()
    crawl_delay: float | None = None

    def allows(self, path_and_query: str) -> bool:
        path = _normalise(path_and_query or "/")
        best: Rule | None = None
        for rule in self.rules:
            if not rule.matches(path):
                continue
            if (
                best is None
                or rule.specificity > best.specificity
                or (rule.specificity == best.specificity and rule.allow and not best.allow)
            ):
                best = rule
        return best is None or best.allow


def parse_robots(text: str, agent_token: str) -> RobotsRules:
    """Parse ``text`` and return the rules for ``agent_token`` (never raises)."""
    token = agent_token.strip().lower()
    groups: list[tuple[list[str], list[Rule], list[float]]] = []
    agents: list[str] = []
    rules: list[Rule] = []
    delays: list[float] = []
    in_agents = False
    total = 0
    for raw in text.lstrip("﻿").splitlines():
        line = raw[:MAX_LINE].split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if not in_agents:
                agents, rules, delays = [], [], []
                groups.append((agents, rules, delays))
                in_agents = True
            agents.append(value.lower())
        elif field in ("allow", "disallow", "crawl-delay") and groups:
            in_agents = False
            if field == "crawl-delay":
                if _DELAY.fullmatch(value):
                    delays.append(min(float(value), MAX_DELAY))
                continue
            total += 1
            if total > MAX_RULES:
                break
            if not value:
                continue  # an empty Disallow (or Allow) changes nothing
            anchored = value.endswith("$")
            pattern = _normalise(value[:-1] if anchored else value)
            if not pattern.startswith(("/", "*")):
                continue
            rules.append(Rule(field == "allow", pattern, anchored))
    chosen = [g for g in groups if token in g[0]] or [g for g in groups if "*" in g[0]]
    merged = tuple(rule for g in chosen for rule in g[1])
    delay_values = [d for g in chosen for d in g[2]]
    return RobotsRules(merged, max(delay_values) if delay_values else None)


def agent_token(user_agent: str) -> str:
    """The product token of a User-Agent string (``ABMEngine/1.0 (+x)`` gives ``ABMEngine``)."""
    first = user_agent.strip().split(" ", 1)[0]
    return first.split("/", 1)[0] or "abmengine"
