"""Department name normalisation onto the framework's department vocabulary."""

from __future__ import annotations

import re
from typing import Final

#: Canonical names, equal to ``apps.integrations.framework.normalized.DEPARTMENTS``; the order
#: is the order used in reports and explanations.
DEPARTMENT_ORDER: Final[tuple[str, ...]] = (
    "sales",
    "bd",
    "marketing",
    "commercial",
    "partnerships",
)

_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "sales": ("sales", "sale", "sales team", "مبيعات", "المبيعات"),
    "bd": (
        "bd",
        "biz dev",
        "bizdev",
        "business development",
        "business dev",
        "bd team",
        "تطوير الاعمال",
        "تطوير الأعمال",
    ),
    "marketing": ("marketing", "marketing team", "تسويق", "التسويق"),
    "commercial": (
        "commercial",
        "commercial team",
        "commercial and partnerships",
        "commercial partnerships",
        "commercial partnership",
        "تجاري",
        "التجاري",
        "القسم التجاري",
    ),
    "partnerships": (
        "partnerships",
        "partnership",
        "partners",
        "alliances",
        "channel partnerships",
        "شراكات",
        "الشراكات",
        "الشراكات الاستراتيجية",
    ),
}

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _key(name: str) -> str:
    return _NON_WORD.sub(" ", name.casefold()).strip()


_LOOKUP: Final[dict[str, str]] = {
    _key(synonym): canonical for canonical, names in _SYNONYMS.items() for synonym in names
}


def normalize_department(name: str | None) -> str | None:
    """Map a free-text department name to a canonical one, or ``None`` when unrecognised.

    Case, punctuation and repeated whitespace are ignored (``"Business-Development"`` matches).
    Compound names such as ``"Sales & Marketing"`` are not split and stay unrecognised. ``None``
    and unknown names both give ``None``; callers that must tell them apart check for ``None``
    first.
    """
    if not name:
        return None
    return _LOOKUP.get(_key(name))
