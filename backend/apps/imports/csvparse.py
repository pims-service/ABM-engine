"""CSV template, column mapping and a streaming, hostile-input-safe parser (issue #61).

Pure: standard library only, no database, no Django. Persistence and queuing are #62.

    it = iter_rows(upload_bytes_or_fileobj)          # mapping suggested from the headers
    it.summary.encoding, it.summary.delimiter, it.headers
    for row in it:                                    # ParsedRow(row_number, raw, mapped, errors)
        if row.ok:
            validate_company_input(row.mapped)
    it.summary                                        # final counts and sha256 (``complete``)

Whole-file problems raise ``CsvFileError`` (stable ``.code``): ``empty_file``, ``no_header``,
``file_too_large``, ``binary_file``, ``unsupported_format`` (xlsx/zip/pdf...), ``undecodable``,
``too_many_columns``, ``header_too_long``, ``too_many_rows``, ``name_column_missing`` and the other
``MappingError`` codes. Bad individual rows never raise: they come back with ``errors`` set.

Template (``build_template_csv``): ``company_name, website, profile_url, country, industry,
notes``. ``industry``, ``notes`` and any other column are not mapped; they stay in ``raw``
(and so in ``ImportRow.raw_data``).

Encoding detection order (deterministic, whole file looked at, never guessed per chunk):

1. BOM: UTF-8, UTF-16 LE/BE, UTF-32 LE/BE.
2. No BOM and NUL bytes in a UTF-16 pattern (NULs only at odd or only at even offsets of the
   first block): UTF-16 LE / BE. Any other NUL byte: ``binary_file``.
3. Strict UTF-8 over the whole file.
4. Otherwise a legacy single-byte export: Windows-1256 when the high bytes are mostly Arabic
   letters, else Windows-1252, else ISO-8859-1 (never fails). Each fallback adds a warning
   (``encoding_fallback``) so the UI can ask the user to check the text.
A declared encoding (BOM) that then fails to decode is ``undecodable``.

Delimiter: comma, semicolon or tab, chosen by which splits the first records into a consistent
number of columns (ties: comma, semicolon, tab). Excel's ``sep=;`` first line is honoured.
Quoted fields, doubled quotes, embedded newlines, CRLF, LF and CR line ends all work.

Row numbers: ``row_number`` 1 is the first record after the header; blank records are skipped
but still consume a number, so it is the physical record position (spreadsheet row = number + 1
when the header is on row 1). A quoted cell with a newline is still one record.

Values: BOM, zero-width and bidi-control characters are removed, surrounding whitespace is
stripped, everything else is kept as typed. Formula-looking values (``=cmd|' /C calc'!A0``) are
NOT altered in ``raw``/``mapped``: pass any value you write to a CSV/XLSX export through
``safe_cell`` (or ``safe_row``), which prefixes a single quote.

Memory: the file is read in 64 KiB blocks twice (once to decide the encoding, once to parse)
and rows are produced lazily; a non-seekable stream is first spooled to a temporary file.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import codecs
import contextlib
import csv
import difflib
import hashlib
import io
import re
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import IO, Any, Protocol, cast


class BinaryReader(Protocol):
    """Anything with ``read(n) -> bytes`` (a binary file, an upload, a stream)."""

    def read(self, size: int = ..., /) -> bytes: ...


Source = bytes | bytearray | memoryview | BinaryReader

# ----------------------------------------------------------------------------- limits

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 50
MAX_CELL_LENGTH = 2000
_BLOCK = 64 * 1024
_SNIFF_RECORDS = 20


@dataclass(frozen=True, slots=True)
class CsvLimits:
    """Configurable ceilings. ``max_rows`` counts non-blank data rows."""

    max_file_bytes: int = MAX_FILE_BYTES
    max_rows: int = MAX_ROWS
    max_columns: int = MAX_COLUMNS
    max_cell_length: int = MAX_CELL_LENGTH


DEFAULT_LIMITS = CsvLimits()

# ----------------------------------------------------------------------------- errors

FILE_EMPTY = "empty_file"
FILE_NO_HEADER = "no_header"
FILE_TOO_LARGE = "file_too_large"
FILE_BINARY = "binary_file"
FILE_UNSUPPORTED = "unsupported_format"
FILE_UNDECODABLE = "undecodable"
FILE_NOT_BINARY = "not_binary"
FILE_TOO_MANY_COLUMNS = "too_many_columns"
FILE_HEADER_TOO_LONG = "header_too_long"
FILE_TOO_MANY_ROWS = "too_many_rows"
NAME_COLUMN_MISSING = "name_column_missing"
MAPPING_UNKNOWN_COLUMN = "unknown_column"
MAPPING_UNKNOWN_FIELD = "unknown_field"
MAPPING_DUPLICATE_TARGET = "duplicate_target"

ROW_COLUMN_COUNT = "column_count_mismatch"
ROW_CELL_TOO_LONG = "cell_too_long"
ROW_MALFORMED = "malformed_row"

WARN_ENCODING_FALLBACK = "encoding_fallback"
WARN_NO_DATA_ROWS = "no_data_rows"
WARN_DUPLICATE_HEADERS = "duplicate_headers"
WARN_BLANK_HEADERS = "blank_headers"

MESSAGES: dict[str, str] = {
    FILE_EMPTY: "The file is empty.",
    FILE_NO_HEADER: "The file has no header row.",
    FILE_TOO_LARGE: "The file is larger than the allowed size.",
    FILE_BINARY: "This does not look like a text CSV file (it contains binary data).",
    FILE_UNSUPPORTED: "This looks like a spreadsheet or another binary format. Save it as CSV.",
    FILE_UNDECODABLE: "The file text could not be decoded. Save it as CSV UTF-8 and try again.",
    FILE_NOT_BINARY: "Provide the file as bytes or a binary file object.",
    FILE_TOO_MANY_COLUMNS: "The file has more columns than the allowed maximum.",
    FILE_HEADER_TOO_LONG: "A column header is longer than the allowed maximum.",
    FILE_TOO_MANY_ROWS: "The file has more rows than the allowed maximum.",
    NAME_COLUMN_MISSING: "Choose which column holds the company name.",
    MAPPING_UNKNOWN_COLUMN: "The mapping refers to a column that is not in the file.",
    MAPPING_UNKNOWN_FIELD: "The mapping targets a field that does not exist.",
    MAPPING_DUPLICATE_TARGET: "Two columns are mapped to the same field.",
    ROW_COLUMN_COUNT: "The row has a different number of columns than the header.",
    ROW_CELL_TOO_LONG: "A cell is longer than the allowed maximum.",
    ROW_MALFORMED: "The row could not be read (broken quoting).",
    WARN_ENCODING_FALLBACK: "The file is not UTF-8; check that the text looks right.",
    WARN_NO_DATA_ROWS: "The file has a header but no data rows.",
    WARN_DUPLICATE_HEADERS: "Some column headers repeat; later ones were renamed.",
    WARN_BLANK_HEADERS: "Some column headers are empty; they were named column_N.",
}


@dataclass(frozen=True, slots=True)
class Issue:
    """A coded problem or warning. ``code`` is stable; ``message`` is for people."""

    code: str
    message: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def _issue(code: str, detail: str = "") -> Issue:
    return Issue(code, MESSAGES[code], detail)


class CsvFileError(ValueError):
    """The whole file (or the mapping) cannot be processed."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = MESSAGES.get(code, code)
        super().__init__(f"{message} ({detail})" if detail else message)

    def as_issue(self) -> Issue:
        return _issue(self.code, self.detail)


class MappingError(CsvFileError):
    """The column mapping is unusable; ``issues`` lists every problem."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        first = issues[0]
        super().__init__(first.code, first.detail)


# ----------------------------------------------------------------------------- text cleaning

_STRIP_CHARS = dict.fromkeys(
    [
        0xFEFF,  # BOM / zero width no-break space
        0x200B,  # zero width space
        0x200C,  # zero width non-joiner
        0x200D,  # zero width joiner
        0x2060,  # word joiner
        0x180E,
        0x00AD,  # soft hyphen
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x061C,  # Arabic letter mark
        *range(0x202A, 0x202F),  # bidi embeddings and overrides
        *range(0x2066, 0x206A),  # bidi isolates
    ]
)
_SPACES = re.compile(r"\s+")


def clean_cell(value: str) -> str:
    """Remove BOM, zero-width and bidi control characters, strip surrounding whitespace."""
    return value.translate(_STRIP_CHARS).strip()


def _clean_header(value: str) -> str:
    return _SPACES.sub(" ", clean_cell(value))


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")


def safe_cell(value: object) -> str:
    """Neutralize CSV/spreadsheet formula injection for a value being exported.

    A value whose first visible character is ``=``, ``+``, ``-``, ``@`` (or a tab/CR, or the
    full-width forms) gets a leading single quote, which spreadsheets show as plain text.
    ``None`` becomes an empty string. Idempotent for already-prefixed values.
    """
    text = "" if value is None else str(value)
    probe = text.lstrip("  ").translate(_STRIP_CHARS)
    if probe.startswith(_FORMULA_START):
        return "'" + text
    return text


def safe_row(values: Iterable[object]) -> list[str]:
    """``safe_cell`` for every value of an export row."""
    return [safe_cell(v) for v in values]


# ----------------------------------------------------------------------------- countries

#: Common English (and a few Arabic) country names accepted in the ``country`` column, mapped to
#: ISO 3166-1 alpha-2. Keys are compared after ``_name_key`` (case, accents and punctuation
#: ignored, a leading "the" dropped). A two letter value is taken as a code as is.
COUNTRY_NAMES: dict[str, str] = {
    "saudi arabia": "SA",
    "kingdom of saudi arabia": "SA",
    "ksa": "SA",
    "united arab emirates": "AE",
    "uae": "AE",
    "emirates": "AE",
    "egypt": "EG",
    "kuwait": "KW",
    "qatar": "QA",
    "bahrain": "BH",
    "oman": "OM",
    "jordan": "JO",
    "lebanon": "LB",
    "iraq": "IQ",
    "syria": "SY",
    "yemen": "YE",
    "palestine": "PS",
    "israel": "IL",
    "turkey": "TR",
    "turkiye": "TR",
    "iran": "IR",
    "morocco": "MA",
    "algeria": "DZ",
    "tunisia": "TN",
    "libya": "LY",
    "sudan": "SD",
    "pakistan": "PK",
    "india": "IN",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "china": "CN",
    "hong kong": "HK",
    "japan": "JP",
    "south korea": "KR",
    "korea": "KR",
    "singapore": "SG",
    "malaysia": "MY",
    "indonesia": "ID",
    "philippines": "PH",
    "thailand": "TH",
    "vietnam": "VN",
    "australia": "AU",
    "new zealand": "NZ",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "ireland": "IE",
    "france": "FR",
    "germany": "DE",
    "italy": "IT",
    "spain": "ES",
    "portugal": "PT",
    "netherlands": "NL",
    "holland": "NL",
    "belgium": "BE",
    "switzerland": "CH",
    "austria": "AT",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "greece": "GR",
    "cyprus": "CY",
    "russia": "RU",
    "ukraine": "UA",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "america": "US",
    "canada": "CA",
    "mexico": "MX",
    "brazil": "BR",
    "argentina": "AR",
    "south africa": "ZA",
    "nigeria": "NG",
    "kenya": "KE",
    "ethiopia": "ET",
    "السعودية": "SA",  # السعودية
    "المملكة العربية السعودية": "SA",
    "الامارات": "AE",  # الإمارات
    "الامارات العربية المتحدة": "AE",
    "مصر": "EG",  # مصر
    "الكويت": "KW",  # الكويت
    "قطر": "QA",  # قطر
    "البحرين": "BH",  # البحرين
    "عمان": "OM",  # عمان
    "الاردن": "JO",  # الأردن
    "لبنان": "LB",  # لبنان
}


def _strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.category(c).startswith("M"))


_ARABIC_FOLD = str.maketrans(
    {
        "أ": "ا",  # alef with hamza above
        "إ": "ا",  # alef with hamza below
        "آ": "ا",  # alef with madda
        "ى": "ي",  # alef maqsura -> yeh
        "ة": "ه",  # teh marbuta -> heh
        "ـ": "",  # tatweel
    }
)
_NON_WORD = re.compile(r"[\W_]+")


def _name_key(text: str) -> str:
    """Comparison key: case, accents, Arabic letter variants, punctuation and spacing folded."""
    folded = _strip_marks(clean_cell(text)).casefold().translate(_ARABIC_FOLD)
    key = _NON_WORD.sub(" ", folded).strip()
    return key[4:] if key.startswith("the ") else key


_COUNTRY_BY_KEY = {_name_key(name): code for name, code in COUNTRY_NAMES.items()}


def normalize_country(value: str) -> str | None:
    """ISO alpha-2 (upper case) for a code or a known country name; ``None`` when unknown.

    Names win over codes (``UK`` -> ``GB``); any other two letter value is returned upper-cased
    without checking it is assigned, the input schema (``apps.imports.schema``) makes that call.
    """
    text = clean_cell(value)
    key = _name_key(text)
    found = _COUNTRY_BY_KEY.get(key)
    if found is None and key and all(len(part) == 1 for part in key.split()):
        found = _COUNTRY_BY_KEY.get(key.replace(" ", ""))  # "U.A.E." -> "uae"
    if found is None and len(text) == 2 and text.isascii() and text.isalpha():
        return text.upper()
    return found


# ----------------------------------------------------------------------------- mapping

#: Canonical company input fields a column can be mapped to (``apps.imports.schema``).
FIELDS = ("name", "website", "profile_url", "country")

#: Header aliases per field. Compared after ``_name_key`` (case, accents, punctuation, Arabic
#: letter variants folded), first exactly, then fuzzily (difflib ratio >= ``FUZZY_CUTOFF``).
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": (
        "name",
        "company",
        "company name",
        "company_name",
        "organisation",
        "organization",
        "organisation name",
        "organization name",
        "org",
        "org name",
        "account",
        "account name",
        "business",
        "business name",
        "firm",
        "employer",
        "اسم الشركة",
        "الشركة",
        "اسم المنشأة",
        "المنشأة",
        "اسم المؤسسة",
        "المؤسسة",
    ),
    "website": (
        "website",
        "web site",
        "web",
        "site",
        "domain",
        "company website",
        "company domain",
        "website url",
        "web address",
        "homepage",
        "home page",
        "url",
        "الموقع",
        "الموقع الالكتروني",
        "موقع الشركة",
        "النطاق",
        "الرابط",
    ),
    "profile_url": (
        "profile url",
        "profile",
        "profile link",
        "company profile",
        "linkedin",
        "linkedin url",
        "linkedin profile",
        "linkedin page",
        "linkedin company page",
        "company linkedin",
        "company linkedin url",
        "لينكدإن",
        "لينكد ان",
        "رابط لينكدإن",
        "رابط الملف",
    ),
    "country": (
        "country",
        "country code",
        "country name",
        "hq country",
        "headquarters country",
        "hq",
        "location country",
        "nation",
        "الدولة",
        "البلد",
        "بلد",
        "دولة المقر",
        "رمز الدولة",
    ),
}

FUZZY_CUTOFF = 0.84
_FUZZY_MIN_LENGTH = 4

_ALIAS_INDEX: dict[str, str] = {}
for _field, _aliases in HEADER_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_INDEX.setdefault(_name_key(_alias), _field)
_ALIAS_KEYS = sorted(_ALIAS_INDEX)


@dataclass(frozen=True, slots=True)
class Mapping:
    """Which file column feeds which company field.

    ``columns`` maps a (cleaned, de-duplicated) header to a field in ``FIELDS``; each field is
    used at most once. ``fuzzy`` lists headers matched only approximately (the UI may highlight
    them) and ``unmapped`` the headers with no field: they are not errors, their values stay in
    ``ParsedRow.raw``. ``to_dict()`` is what to store in ``ImportBatch.column_mapping``.
    """

    columns: dict[str, str]
    unmapped: tuple[str, ...] = ()
    fuzzy: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, str]:
        return dict(self.columns)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Mapping:
        """Build from a UI/stored ``{header: field}`` payload (not yet validated)."""
        return cls(columns={str(k): str(v) for k, v in data.items()})

    def header_for(self, field_name: str) -> str | None:
        for header, target in self.columns.items():
            if target == field_name:
                return header
        return None


def suggest_mapping(headers: Iterable[str]) -> Mapping:
    """Guess the mapping from header names (exact aliases first, then close spellings)."""
    cleaned = [_clean_header(h) for h in headers]
    taken: dict[str, str] = {}  # field -> header
    fuzzy: list[str] = []
    keys = [_name_key(h) for h in cleaned]

    for header, key in zip(cleaned, keys, strict=True):
        target = _ALIAS_INDEX.get(key)
        if target and target not in taken:
            taken[target] = header

    for header, key in zip(cleaned, keys, strict=True):
        if header in taken.values() or len(key) < _FUZZY_MIN_LENGTH:
            continue
        for candidate in difflib.get_close_matches(key, _ALIAS_KEYS, n=3, cutoff=FUZZY_CUTOFF):
            target = _ALIAS_INDEX[candidate]
            if target not in taken:
                taken[target] = header
                fuzzy.append(header)
                break

    columns = {header: target for target, header in taken.items()}
    ordered = {h: columns[h] for h in cleaned if h in columns}
    unmapped = tuple(h for h in cleaned if h not in ordered)
    return Mapping(columns=ordered, unmapped=unmapped, fuzzy=tuple(fuzzy))


def validate_mapping(mapping: Mapping, headers: Iterable[str]) -> list[Issue]:
    """Every problem with ``mapping`` against the file's headers (empty list means usable).

    Codes: ``name_column_missing``, ``unknown_column`` (mapped header not in the file),
    ``unknown_field`` (target not in ``FIELDS``), ``duplicate_target`` (field mapped twice).
    """
    header_set = set(headers)
    issues: list[Issue] = []
    seen: dict[str, str] = {}
    for header, target in mapping.columns.items():
        if header not in header_set:
            issues.append(_issue(MAPPING_UNKNOWN_COLUMN, header))
        if target not in FIELDS:
            issues.append(_issue(MAPPING_UNKNOWN_FIELD, target))
        elif target in seen:
            issues.append(_issue(MAPPING_DUPLICATE_TARGET, target))
        else:
            seen[target] = header
    if "name" not in seen:
        issues.append(_issue(NAME_COLUMN_MISSING))
    return issues


def apply_mapping(row: dict[str, str], mapping: Mapping) -> dict[str, str]:
    """The company fields of one raw row: ``{field: value}`` for each mapped column.

    ``country`` names become ISO alpha-2 when known (``normalize_country``); anything else is
    passed on as is for the schema to judge. Missing cells give ``""``.
    """
    out: dict[str, str] = {}
    for header, target in mapping.columns.items():
        value = row.get(header, "")
        if target == "country" and value:
            value = normalize_country(value) or value
        out[target] = value
    return out


# ----------------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """One data row. ``raw`` has every column (header -> cleaned text); ``mapped`` only the
    company fields and is empty when ``errors`` is not."""

    row_number: int
    raw: dict[str, str]
    mapped: dict[str, str]
    errors: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class ParseSummary:
    """Totals for the file. Final once the iterator is exhausted (``complete``)."""

    encoding: str = ""
    delimiter: str = ","
    headers: list[str] = field(default_factory=list)
    mapping: Mapping | None = None
    file_size: int = 0
    file_sha256: str = ""
    total_rows: int = 0  # non-blank data rows (good and bad)
    blank_rows: int = 0
    error_rows: int = 0
    warnings: list[Issue] = field(default_factory=list)
    complete: bool = False

    @property
    def ok_rows(self) -> int:
        return self.total_rows - self.error_rows


# ----------------------------------------------------------------------------- decoding

_ARABIC_1256 = (
    set(range(0xC1, 0xD7)) | set(range(0xD8, 0xE0)) | {0xE1, 0xE3, 0xE4, 0xE5, 0xE6, 0xEC, 0xED}
)
_NOT_ARABIC_1256 = bytes(b for b in range(256) if b not in _ARABIC_1256)
_ASCII = bytes(range(128))
_ARABIC_SHARE = 0.6

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"PK\x03\x04",  # zip, xlsx, docx
    b"\xd0\xcf\x11\xe0",  # legacy xls/doc
    b"%PDF",
    b"\x1f\x8b",  # gzip
    b"\x89PNG",
    b"GIF8",
    b"\xff\xd8\xff",  # jpeg
    b"MZ",  # executables
)


class _Detection:
    __slots__ = ("encoding", "head", "size", "warnings")

    def __init__(self) -> None:
        self.encoding = "utf-8"
        self.head = b""
        self.size = 0
        self.warnings: list[Issue] = []


def _read_block(source: BinaryReader) -> bytes:
    data = source.read(_BLOCK)
    if not isinstance(data, bytes | bytearray | memoryview):
        raise CsvFileError(FILE_NOT_BINARY)
    return bytes(data)


def _utf16_guess(block: bytes) -> str | None:
    sample = block[:4096]
    if len(sample) < 2:
        return None
    even, odd = sample[0::2], sample[1::2]
    if odd.count(0) >= len(odd) * 0.3 and even.count(0) == 0:
        return "utf-16-le"
    if even.count(0) >= len(even) * 0.3 and odd.count(0) == 0:
        return "utf-16-be"
    return None


def _detect(source: IO[bytes], limits: CsvLimits) -> _Detection:
    """First pass: size, encoding, binary checks. Leaves ``source`` after the last block."""
    det = _Detection()
    first = _read_block(source)
    if not first:
        raise CsvFileError(FILE_EMPTY)
    det.head = first
    forced: str | None = None
    for bom, name in _BOMS:
        if first.startswith(bom):
            forced = name
            break
    if forced is None:
        if first.startswith(_BINARY_SIGNATURES):
            raise CsvFileError(FILE_UNSUPPORTED)
        if b"\x00" in first:
            forced = _utf16_guess(first)
            if forced is None:
                raise CsvFileError(FILE_BINARY)

    if forced is not None:
        decoder = codecs.getincrementaldecoder(forced)(errors="strict")
        det.encoding = forced
    else:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")

    utf8_ok = True
    high = arabic = 0
    block: bytes = first
    while block:
        det.size += len(block)
        if det.size > limits.max_file_bytes:
            raise CsvFileError(FILE_TOO_LARGE, f"limit {limits.max_file_bytes} bytes")
        if forced is None:
            if b"\x00" in block:
                raise CsvFileError(FILE_BINARY)
            hi = block.translate(None, _ASCII)
            high += len(hi)
            arabic += len(hi.translate(None, _NOT_ARABIC_1256))
        if utf8_ok or forced is not None:
            try:
                text = decoder.decode(block)
            except UnicodeDecodeError as exc:
                if forced is not None:
                    raise CsvFileError(FILE_UNDECODABLE, det.encoding) from exc
                utf8_ok = False
            else:
                if forced is not None and "\x00" in text:
                    raise CsvFileError(FILE_BINARY)
        block = _read_block(source)
    if forced is not None:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise CsvFileError(FILE_UNDECODABLE, det.encoding) from exc
        return det
    if utf8_ok:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            utf8_ok = False
    if utf8_ok:
        return det

    # Legacy single-byte export.
    if high and arabic / high >= _ARABIC_SHARE:
        det.encoding = "cp1256"
    else:
        det.encoding = "cp1252" if _decodes(source, "cp1252") else "latin-1"
    det.warnings.append(_issue(WARN_ENCODING_FALLBACK, det.encoding))
    return det


def _decodes(source: IO[bytes], encoding: str) -> bool:
    """Whether the whole file decodes strictly as ``encoding`` (re-reads from the start)."""
    source.seek(0)
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    try:
        block = _read_block(source)
        while block:
            decoder.decode(block)
            block = _read_block(source)
    except UnicodeDecodeError:
        return False
    return True


class _CountingReader(io.RawIOBase):
    """Binary reader that hashes and counts what passes through and enforces the size limit."""

    def __init__(self, source: IO[bytes], limit: int) -> None:
        self._source = source
        self._limit = limit
        self.sha = hashlib.sha256()
        self.size = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._source.read(len(buffer))
        if not data:
            return 0
        self.size += len(data)
        if self.size > self._limit:
            raise CsvFileError(FILE_TOO_LARGE, f"limit {self._limit} bytes")
        self.sha.update(data)
        buffer[: len(data)] = data
        return len(data)


def _open_source(source: Source, limits: CsvLimits) -> tuple[IO[bytes], IO[bytes] | None]:
    """A seekable binary stream positioned at the start, plus a temp file to close (or None)."""
    if isinstance(source, bytes | bytearray | memoryview):
        data = bytes(source) if not isinstance(source, bytes) else source
        if len(data) > limits.max_file_bytes:
            raise CsvFileError(FILE_TOO_LARGE, f"limit {limits.max_file_bytes} bytes")
        return io.BytesIO(data), None
    if not hasattr(source, "read"):
        raise CsvFileError(FILE_NOT_BINARY)
    seekable = False
    stream = cast(IO[bytes], source)
    try:
        seekable = bool(stream.seekable())
    except (AttributeError, ValueError, OSError):
        seekable = False
    if seekable:
        start = stream.tell()
        end = stream.seek(0, io.SEEK_END)
        stream.seek(start)
        if end - start > limits.max_file_bytes:
            raise CsvFileError(FILE_TOO_LARGE, f"limit {limits.max_file_bytes} bytes")
        if start != 0:
            # Parse from the current position as if it were the beginning.
            moved = tempfile.SpooledTemporaryFile(max_size=_BLOCK * 16)  # noqa: SIM115
            _copy(stream, moved, limits)
            return moved, moved
        return stream, None
    spool = tempfile.SpooledTemporaryFile(max_size=_BLOCK * 16)  # noqa: SIM115
    _copy(source, spool, limits)
    return spool, spool


def _copy(source: BinaryReader, spool: IO[bytes], limits: CsvLimits) -> None:
    total = 0
    try:
        block = _read_block(source)
        while block:
            total += len(block)
            if total > limits.max_file_bytes:
                raise CsvFileError(FILE_TOO_LARGE, f"limit {limits.max_file_bytes} bytes")
            spool.write(block)
            block = _read_block(source)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)


# ----------------------------------------------------------------------------- delimiter

_DELIMITERS = (",", ";", "\t")
_SEP_HINT = re.compile(r"^sep=(.)(?:\r\n|\r|\n|$)")


def _sniff_delimiter(sample: str) -> str:
    hint = _SEP_HINT.match(sample)
    if hint and hint.group(1) in _DELIMITERS:
        return hint.group(1)
    if hint:
        sample = sample[hint.end() :]
    # Drop a possibly cut-off last line.
    if len(sample) >= _BLOCK // 2 and "\n" in sample:
        sample = sample[: sample.rstrip("\r\n").rfind("\n") + 1]
    best = ","
    best_score = (-1, -1)
    for delim in _DELIMITERS:
        try:
            records = []
            for record in csv.reader(io.StringIO(sample, newline=""), delimiter=delim):
                if any(c.strip() for c in record):
                    records.append(len(record))
                if len(records) >= _SNIFF_RECORDS:
                    break
        except csv.Error:
            continue
        if not records or records[0] < 2:
            continue
        consistent = sum(1 for n in records if n == records[0])
        score = (consistent, records[0])
        if score > best_score:
            best, best_score = delim, score
    return best


# ----------------------------------------------------------------------------- parser


def _dedupe_headers(raw_headers: list[str], warnings: list[Issue]) -> list[str]:
    headers: list[str] = []
    seen: set[str] = set()
    renamed = blank = False
    for index, value in enumerate(raw_headers, start=1):
        name = _clean_header(value)
        if not name:
            name = f"column_{index}"
            blank = True
        base, n = name, 2
        while name in seen:
            name = f"{base} ({n})"
            n += 1
            renamed = True
        seen.add(name)
        headers.append(name)
    if renamed:
        warnings.append(_issue(WARN_DUPLICATE_HEADERS))
    if blank:
        warnings.append(_issue(WARN_BLANK_HEADERS))
    return headers


class CsvRowIterator:
    """Iterator of ``ParsedRow``; the file is opened and the header read at construction.

    Attributes available immediately: ``headers``, ``mapping``, ``summary`` (encoding,
    delimiter, warnings, headers). ``summary`` totals, size and sha256 are final once iteration
    ends (``summary.complete``). Use as a context manager or call ``close()`` to release the
    temporary file early (it is released automatically when the iteration ends).
    """

    def __init__(
        self,
        source: Source,
        mapping: Mapping | None = None,
        limits: CsvLimits = DEFAULT_LIMITS,
        delimiter: str | None = None,
    ) -> None:
        self.limits = limits
        self.summary = ParseSummary()
        self._spool: IO[bytes] | None = None
        self._text: io.TextIOWrapper | None = None
        self._counter: _CountingReader | None = None
        self._records: Any = None
        self._closed = False
        try:
            self._open(source, mapping, delimiter)
        except BaseException:
            self.close()
            raise
        self._gen = self._rows()

    # -- setup

    def _open(
        self,
        source: Source,
        mapping: Mapping | None,
        delimiter: str | None,
    ) -> None:
        stream, self._spool = _open_source(source, self.limits)
        det = _detect(stream, self.limits)
        stream.seek(0)
        self.summary.encoding = det.encoding
        self.summary.warnings.extend(det.warnings)

        head_text = det.head.decode(det.encoding, errors="ignore").lstrip("﻿")
        if delimiter is not None and delimiter not in _DELIMITERS:
            raise ValueError(f"delimiter must be one of {_DELIMITERS!r}")
        self.summary.delimiter = delimiter or _sniff_delimiter(head_text)

        self._counter = _CountingReader(stream, self.limits.max_file_bytes)
        self._text = io.TextIOWrapper(
            io.BufferedReader(self._counter, buffer_size=_BLOCK),
            encoding=det.encoding,
            errors="strict",
            newline="",
        )
        if _SEP_HINT.match(head_text):
            self._read_line()
        self._records = csv.reader(self._text, delimiter=self.summary.delimiter, strict=False)

        raw_headers = self._read_header()
        self.headers = _dedupe_headers(raw_headers, self.summary.warnings)
        self.summary.headers = list(self.headers)
        if mapping is None:
            mapping = suggest_mapping(self.headers)
        issues = validate_mapping(mapping, self.headers)
        if issues:
            raise MappingError(issues)
        self.mapping = mapping
        self.summary.mapping = mapping

    def _read_line(self) -> None:
        if self._text is None:  # pragma: no cover - set before any call
            return
        try:
            self._text.readline()
        except UnicodeDecodeError as exc:
            raise CsvFileError(FILE_UNDECODABLE, self.summary.encoding) from exc

    def _next_record(self) -> list[str] | None:
        try:
            return next(self._records, None)
        except UnicodeDecodeError as exc:
            raise CsvFileError(FILE_UNDECODABLE, self.summary.encoding) from exc

    def _read_header(self) -> list[str]:
        while True:
            try:
                record = self._next_record()
            except csv.Error:
                raise CsvFileError(FILE_NO_HEADER, "broken quoting in the header") from None
            if record is None:
                raise CsvFileError(FILE_NO_HEADER)
            if any(clean_cell(c) for c in record):
                break
        if len(record) > self.limits.max_columns:
            raise CsvFileError(FILE_TOO_MANY_COLUMNS, f"limit {self.limits.max_columns}")
        if any(len(c) > self.limits.max_cell_length for c in record):
            raise CsvFileError(FILE_HEADER_TOO_LONG, f"limit {self.limits.max_cell_length}")
        return record

    # -- iteration

    def __iter__(self) -> CsvRowIterator:
        return self

    def __next__(self) -> ParsedRow:
        return next(self._gen)

    def __enter__(self) -> CsvRowIterator:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._text is not None:
            with contextlib.suppress(ValueError, OSError):
                self._text.detach()
        if self._spool is not None:
            self._spool.close()

    def _finish(self, exhausted: bool) -> None:
        summary = self.summary
        if self._counter is not None:
            summary.file_size = self._counter.size
            summary.file_sha256 = self._counter.sha.hexdigest()
        if exhausted:
            if summary.total_rows == 0:
                summary.warnings.append(_issue(WARN_NO_DATA_ROWS))
            summary.complete = True

    def _rows(self) -> Iterator[ParsedRow]:
        summary, headers = self.summary, self.headers
        width = len(headers)
        number = 0
        exhausted = False
        try:
            while True:
                try:
                    record = self._next_record()
                except csv.Error:
                    number += 1
                    summary.total_rows += 1
                    summary.error_rows += 1
                    self._check_row_limit()
                    yield ParsedRow(number, {}, {}, (_issue(ROW_MALFORMED),))
                    continue
                if record is None:
                    exhausted = True
                    break
                number += 1
                cells = [clean_cell(c) for c in record]
                if not any(cells):
                    summary.blank_rows += 1
                    continue
                summary.total_rows += 1
                self._check_row_limit()
                yield self._build(number, cells, width)
        finally:
            self._finish(exhausted)
            self.close()

    def _check_row_limit(self) -> None:
        if self.summary.total_rows > self.limits.max_rows:
            raise CsvFileError(FILE_TOO_MANY_ROWS, f"limit {self.limits.max_rows}")

    def _build(self, number: int, cells: list[str], width: int) -> ParsedRow:
        limit = self.limits.max_cell_length
        errors: list[Issue] = []
        if len(cells) != width:
            errors.append(_issue(ROW_COLUMN_COUNT, f"expected {width}, found {len(cells)}"))
        long_cols = [i for i, c in enumerate(cells) if len(c) > limit]
        if long_cols:
            first = self.headers[long_cols[0]] if long_cols[0] < width else str(long_cols[0] + 1)
            errors.append(_issue(ROW_CELL_TOO_LONG, first))
        if errors:
            self.summary.error_rows += 1
            raw = {h: c[:limit] for h, c in zip(self.headers, cells, strict=False)}
            return ParsedRow(number, raw, {}, tuple(errors))
        raw = dict(zip(self.headers, cells, strict=True))
        return ParsedRow(number, raw, apply_mapping(raw, self.mapping), ())


def iter_rows(
    source: Source,
    mapping: Mapping | None = None,
    limits: CsvLimits = DEFAULT_LIMITS,
    *,
    delimiter: str | None = None,
) -> CsvRowIterator:
    """Stream ``ParsedRow`` objects from CSV bytes or a binary file object.

    ``mapping=None`` uses ``suggest_mapping``; a mapping without a name column raises
    ``MappingError``. Raises ``CsvFileError`` at call time for whole-file problems visible in the
    header or size, and mid-iteration for ``too_many_rows`` / a size overrun on a stream whose
    size was unknown. Run ``summarize`` first to validate a file end to end.
    """
    return CsvRowIterator(source, mapping, limits, delimiter)


def summarize(
    source: Source,
    mapping: Mapping | None = None,
    limits: CsvLimits = DEFAULT_LIMITS,
    *,
    delimiter: str | None = None,
) -> ParseSummary:
    """Parse the whole file (constant memory) and return the final summary.

    Raises ``CsvFileError`` for any whole-file problem, so a caller can reject a bad upload
    before queuing work. ``summary.file_sha256`` is the hash of the exact bytes.
    """
    with iter_rows(source, mapping, limits, delimiter=delimiter) as rows:
        for _ in rows:
            pass
        return rows.summary


@dataclass(frozen=True, slots=True)
class Preview:
    """First rows of a file with the detected settings, for the mapping screen (#64)."""

    headers: list[str]
    mapping: Mapping
    rows: list[ParsedRow]
    summary: ParseSummary  # not complete: file totals need ``summarize``


def preview(
    source: Source,
    mapping: Mapping | None = None,
    limits: CsvLimits = DEFAULT_LIMITS,
    *,
    count: int = 10,
) -> Preview:
    """The first ``count`` non-blank rows plus headers, suggested mapping, encoding, delimiter."""
    with iter_rows(source, mapping, limits) as it:
        rows = []
        for row in it:
            rows.append(row)
            if len(rows) >= count:
                break
        return Preview(list(it.headers), it.mapping, rows, it.summary)


# ----------------------------------------------------------------------------- template

TEMPLATE_COLUMNS = ("company_name", "website", "profile_url", "country", "industry", "notes")
TEMPLATE_FILENAME = "companies-template.csv"
TEMPLATE_CONTENT_TYPE = "text/csv; charset=utf-8"

_SAMPLE_ROWS = (
    (
        "Example Trading Co",
        "example.com",
        "https://www.linkedin.com/company/example-trading",
        "SA",
        "Retail",
        "Placeholder row",
    ),
    (
        "Example Logistics",
        "https://logistics.example.com",
        "",
        "United Arab Emirates",
        "Logistics",
        "",
    ),
    (
        "شركة المثال للتجارة",
        "arabic.example.com",
        "",
        "EG",
        "",
        "Arabic name",
    ),
)


def build_template_csv(*, include_examples: bool = False) -> bytes:
    """The downloadable template: UTF-8 with BOM (Excel shows Arabic correctly), CRLF.

    Header only by default; ``include_examples=True`` adds three placeholder rows
    (``docs/samples/companies-template.csv`` is exactly this output).
    """
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(TEMPLATE_COLUMNS)
    if include_examples:
        writer.writerows(_SAMPLE_ROWS)
    return codecs.BOM_UTF8 + out.getvalue().encode("utf-8")
