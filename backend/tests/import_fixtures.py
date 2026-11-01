"""CSV files for the import tests (issue #70), built in code so the repository stays small.

* ``clean_csv(n)``      n valid, unique companies (the 50, 100, 500 and 5,000-row files).
* ``arabic_csv(n)``     company names in Arabic script only, with ASCII websites.
* ``latin1_csv()``      a file saved in a legacy single-byte encoding (cp1252) with accents.
* ``messy_csv()``       blanks, duplicates, bad URLs, a missing name, odd spacing and a formula-like
                        name, with the counters a correct import must end with.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = "company_name,website,country"

#: The domain the messy file shares with a company that already exists in the campaign.
EXISTING_DOMAIN = "existing-co.example.com"


def clean_csv(rows: int, *, start: int = 1) -> bytes:
    """``rows`` valid companies named ``Company n`` on ``companyn.example.com``."""
    lines = [HEADER]
    lines.extend(f"Company {n},company{n}.example.com,SA" for n in range(start, start + rows))
    return ("\n".join(lines) + "\n").encode()


_ARABIC_NAMES = (
    "شركة النور للتجارة",
    "مؤسسة الرياض للخدمات اللوجستية",
    "مجموعة الخليج للتقنية",
    "الشركة السعودية للأغذية",
    "مصنع الأمل للبلاستيك",
    "شركة الواحة للتسويق",
    "مؤسسة الفجر للمقاولات",
    "شركة البحر الأحمر للشحن",
)


def arabic_csv(rows: int = 40) -> bytes:
    """Arabic-only names (each made unique with an Arabic-Indic number) on ASCII domains."""
    digits = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    lines = [HEADER]
    for n in range(1, rows + 1):
        name = f"{_ARABIC_NAMES[n % len(_ARABIC_NAMES)]} {str(n).translate(digits)}"
        lines.append(f"{name},arabic-co-{n}.example.com,SA")
    return ("\n".join(lines) + "\n").encode("utf-8")


def latin1_csv() -> bytes:
    """Names with accents saved as cp1252 (what an old spreadsheet export gives)."""
    text = (
        f"{HEADER}\n"
        "Société Générale Logistique,societe-generale.example.com,FR\n"
        "Müller & Söhne GmbH,mueller-soehne.example.com,DE\n"
        "Peña Hermanos S.A.,pena-hermanos.example.com,ES\n"
    )
    return text.encode("cp1252")


@dataclass(frozen=True)
class MessyFile:
    content: bytes
    total: int  # non-blank data rows
    created: int
    duplicate: int
    failed: int
    skipped: int = 0


def messy_csv() -> MessyFile:
    """A file a person would really send. ``EXISTING_DOMAIN`` must already be in the campaign.

    Rows (blank lines are not counted):
    created  Acme Logistics, Beta Freight (www, path and query stripped), the formula-like name
    duplicate  Acme again (same domain inside the file), Existing Co (domain already in the DB)
    failed   a website that is not a URL, a missing name, a website that is only a scheme
    """
    lines = [
        "﻿" + HEADER,  # a BOM, as Excel writes it
        "Acme Logistics,acme-log.example.com,SA",
        "  Beta Freight  ,https://www.beta-freight.example.com/about?utm=1,sa",
        "",
        "Acme Logistics,acme-log.example.com,SA",
        "Gamma Retail,not a url at all,SA",
        ",orphan.example.com,SA",
        "Delta Foods,http://,SA",
        "=cmd|' /C calc'!A0,formula-co.example.com,SA",
        f"Existing Co,{EXISTING_DOMAIN},SA",
        "",
    ]
    return MessyFile(
        content="\r\n".join(lines).encode("utf-8"),
        total=8,
        created=3,
        duplicate=2,
        failed=3,
    )
