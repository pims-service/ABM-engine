"""Which commercial department does a job title belong to (issue #96, Brief sections 5, 6, 10).

``classify_title("Business Development Manager")`` answers ``bd`` with high confidence;
``classify_title("Head of Growth")`` answers a guess flagged low confidence; a title that matches
nothing is ``unclassified``. Nothing is ever put into Sales by default: unknown stays unknown.

The rules are data (``apps/research/data/department_rules.json``, or the file named by
``settings.RESEARCH_DEPARTMENT_RULES_FILE``), so they can be corrected without a deploy. The file
is read again when it changes on disk. Matching ignores case and punctuation, Arabic diacritics,
the letter variants ``أإآ`` and ``ى``/``ة``, and the Arabic prefix ``ال``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from django.conf import settings

UNCLASSIFIED: Final = "unclassified"
#: Canonical department names, in the order a tie is broken.
DEPARTMENTS: Final = ("sales", "bd", "marketing", "commercial", "partnerships")
HIGH: Final = "high"
LOW: Final = "low"
NONE: Final = "none"

_DIACRITICS = re.compile("[ً-ٰٟـ]")  # tashkeel and tatweel
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_ARABIC_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})  # noqa: RUF001


@dataclass(frozen=True)
class TitleClass:
    """The department of a title and how sure we are. Only ``confidence == "high"`` counts."""

    department: str  # one of DEPARTMENTS or UNCLASSIFIED
    confidence: str  # "high", "low" or "none"
    rule: str | None = None  # the phrase that matched, or ``multiple:a+b``

    @property
    def counts(self) -> bool:
        return self.confidence == HIGH


def fold(text: str) -> list[str]:
    """``text`` as lower-case words with Arabic spelling variants folded together."""
    cleaned = _DIACRITICS.sub("", text.casefold()).translate(_ARABIC_LETTERS)
    return [word for word in _NON_WORD.split(cleaned) if word]


@dataclass(frozen=True)
class _Rules:
    departments: dict[str, tuple[tuple[str, ...], ...]]
    ambiguous: dict[tuple[str, ...], str]


_cache: tuple[Path, float, _Rules] | None = None


def rules_path() -> Path:
    configured = getattr(settings, "RESEARCH_DEPARTMENT_RULES_FILE", "")
    return (
        Path(configured) if configured else Path(__file__).parent / "data" / "department_rules.json"
    )


def _load(path: Path) -> _Rules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    departments: dict[str, tuple[tuple[str, ...], ...]] = {}
    for name, phrases in raw["departments"].items():
        if name not in DEPARTMENTS:
            raise ValueError(f"unknown department {name!r} in {path.name}")
        departments[name] = tuple(tuple(fold(p)) for p in phrases if fold(p))
    ambiguous: dict[tuple[str, ...], str] = {}
    for phrase, name in raw.get("ambiguous", {}).items():
        if name not in DEPARTMENTS:
            raise ValueError(f"unknown department {name!r} for {phrase!r} in {path.name}")
        if fold(phrase):
            ambiguous[tuple(fold(phrase))] = name
    return _Rules(departments, ambiguous)


def rules() -> _Rules:
    """The current rules, read again when the file changed."""
    global _cache
    path = rules_path()
    mtime = path.stat().st_mtime
    if _cache is None or _cache[0] != path or _cache[1] != mtime:
        _cache = (path, mtime, _load(path))
    return _cache[2]


def _word_matches(word: str, wanted: str) -> bool:
    # "المبيعات" is "ال" + "مبيعات": the article does not change the department.
    return word == wanted or (word.startswith("ال") and word[2:] == wanted)


def _contains(words: list[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(
        all(_word_matches(words[i + j], phrase[j]) for j in range(size))
        for i in range(len(words) - size + 1)
    )


def classify_title(title: str | None) -> TitleClass:
    """The department of a job title (see the module docstring)."""
    words = fold(title or "")
    if not words:
        return TitleClass(UNCLASSIFIED, NONE)
    current = rules()
    matched: dict[str, str] = {}
    for department in DEPARTMENTS:
        for phrase in current.departments.get(department, ()):
            if _contains(words, phrase):
                matched.setdefault(department, " ".join(phrase))
    if len(matched) == 1:
        ((only, matched_phrase),) = matched.items()
        return TitleClass(only, HIGH, matched_phrase)
    if len(matched) > 1:
        first = next(d for d in DEPARTMENTS if d in matched)
        return TitleClass(first, LOW, "multiple:" + "+".join(matched))
    for phrase, department in current.ambiguous.items():
        if _contains(words, phrase):
            return TitleClass(department, LOW, " ".join(phrase))
    return TitleClass(UNCLASSIFIED, NONE)


def tally(titles: list[str | None]) -> dict[str, int]:
    """How many titles fall in each department. Low-confidence and unmatched titles are counted
    as ``unclassified`` and never added to a department."""
    counts = dict.fromkeys((*DEPARTMENTS, UNCLASSIFIED), 0)
    for title in titles:
        result = classify_title(title)
        counts[result.department if result.counts else UNCLASSIFIED] += 1
    return counts
