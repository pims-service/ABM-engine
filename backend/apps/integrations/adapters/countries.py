"""Country names for company-data providers that speak in names, not ISO codes.

Apollo filters by country name and answers with one; People Data Labs answers with a lower case
name. The rest of the app uses ISO 3166-1 alpha-2 codes. The names come from the table the CSV
import already uses (``apps.imports.csvparse.COUNTRY_NAMES``), so the two never disagree.
"""

from __future__ import annotations


def to_iso(name: object) -> str | None:
    """ISO alpha-2 for a country name or code, or ``None`` when it is not a known country."""
    if not isinstance(name, str) or not name.strip():
        return None
    from apps.imports.csvparse import normalize_country  # imported late: a heavy module

    return normalize_country(name)


def to_name(code: str) -> str | None:
    """A country's English name for an ISO alpha-2 code, or ``None`` when it is not in the table.

    The first name listed for the code in the shared table is the plain English one.
    """
    from apps.imports.csvparse import COUNTRY_NAMES

    wanted = code.strip().upper()
    for name, iso in COUNTRY_NAMES.items():
        if iso == wanted:
            return name
    return None
