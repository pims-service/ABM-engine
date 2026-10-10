"""CSV template, mapping and streaming parser (issue #61). Pure: no database."""
# ruff: noqa: RUF001, S311

from __future__ import annotations

import codecs
import hashlib
import io
import random
import tracemalloc
from pathlib import Path

import pytest

from apps.campaigns.reference import ISO_3166_ALPHA2
from apps.imports import csvparse as c
from apps.imports.csvparse import (
    CsvFileError,
    CsvLimits,
    Mapping,
    MappingError,
    apply_mapping,
    build_template_csv,
    iter_rows,
    normalize_country,
    preview,
    safe_cell,
    safe_row,
    suggest_mapping,
    summarize,
    validate_mapping,
)

ARABIC = "شركة المثال للتجارة"
HEADER = "company_name,website,country\n"


def rows_of(data, **kw):
    return list(iter_rows(data, **kw))


def codes(row):
    return [e.code for e in row.errors]


# ------------------------------------------------------------------ encodings


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        (HEADER.encode() + f"{ARABIC},a.example.com,SA\n".encode(), "utf-8"),
        (codecs.BOM_UTF8 + HEADER.encode() + f"{ARABIC},a.example.com,SA\n".encode(), "utf-8-sig"),
        (
            codecs.BOM_UTF16_LE + (HEADER + f"{ARABIC},a.example.com,SA\n").encode("utf-16-le"),
            "utf-16",
        ),
        (
            codecs.BOM_UTF16_BE + (HEADER + f"{ARABIC},a.example.com,SA\n").encode("utf-16-be"),
            "utf-16",
        ),
        ((HEADER + f"{ARABIC},a.example.com,SA\n").encode("utf-16-le"), "utf-16-le"),
        ((HEADER + f"{ARABIC},a.example.com,SA\n").encode("utf-16-be"), "utf-16-be"),
        (codecs.BOM_UTF32_LE + (HEADER + f"{ARABIC},a,SA\n").encode("utf-32-le"), "utf-32"),
    ],
)
def test_unicode_encodings_roundtrip_arabic(data, encoding):
    it = iter_rows(data)
    rows = list(it)
    assert it.summary.encoding == encoding
    assert [r.raw["company_name"] for r in rows] == [ARABIC]
    assert it.summary.warnings == []


def test_windows_1252_fallback_with_warning():
    data = "company_name,country\nCafé Münster,DE\nNaïve Ltd,FR\n".encode("cp1252")
    it = iter_rows(data)
    rows = list(it)
    assert it.summary.encoding == "cp1252"
    assert rows[0].raw["company_name"] == "Café Münster"
    assert [w.code for w in it.summary.warnings] == [c.WARN_ENCODING_FALLBACK]


def test_windows_1256_arabic_detected():
    data = f"اسم الشركة,الدولة\n{ARABIC},مصر\n".encode("cp1256")
    it = iter_rows(data)
    rows = list(it)
    assert it.summary.encoding == "cp1256"
    assert rows[0].raw["اسم الشركة"] == ARABIC
    assert rows[0].mapped == {"name": ARABIC, "country": "EG"}


def test_latin1_when_cp1252_cannot_decode():
    data = b"name\nacme \x81 corp\n"
    it = iter_rows(data)
    rows = list(it)
    assert it.summary.encoding == "latin-1"
    assert rows[0].raw["name"] == "acme \x81 corp"


def test_late_non_utf8_byte_is_still_detected():
    data = b"name\n" + b"acme\n" * 40000 + b"caf\xe9\n"
    it = iter_rows(data, limits=CsvLimits(max_rows=10**6))
    rows = list(it)
    assert it.summary.encoding == "cp1252"
    assert rows[-1].raw["name"] == "café"


@pytest.mark.parametrize(
    "data",
    [
        codecs.BOM_UTF8 + b"name\n\xff\xfe broken\n",
        codecs.BOM_UTF16_LE + b"n\x00a\x00m\x00e\x00\n",  # odd length tail
        codecs.BOM_UTF16_LE + b"\x00\xd8\x41\x00",  # lone surrogate
    ],
)
def test_declared_encoding_that_fails_is_undecodable(data):
    with pytest.raises(CsvFileError) as exc:
        summarize(data)
    assert exc.value.code == c.FILE_UNDECODABLE


# ------------------------------------------------------------------ delimiters, quoting


@pytest.mark.parametrize("delim", [",", ";", "\t"])
@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
def test_delimiters_and_line_endings(delim, eol):
    text = eol.join(
        [
            delim.join(["company_name", "website", "country"]),
            delim.join(["Acme", "acme.example.com", "SA"]),
            delim.join([ARABIC, "b.example.com", "AE"]),
        ]
    )
    it = iter_rows(text.encode())
    rows = list(it)
    assert it.summary.delimiter == delim
    assert [r.mapped["name"] for r in rows] == ["Acme", ARABIC]
    assert [r.row_number for r in rows] == [1, 2]


def test_quoted_fields_embedded_newlines_and_delimiters():
    text = 'company_name,notes\r\n"Acme, Inc.","line1\r\nline2"\r\n"Say ""hi""",x\r\nplain,y\r\n'
    rows = rows_of(text.encode())
    assert rows[0].raw == {"company_name": "Acme, Inc.", "notes": "line1\r\nline2"}
    assert rows[1].raw["company_name"] == 'Say "hi"'
    assert [r.row_number for r in rows] == [1, 2, 3]


def test_semicolon_inside_quotes_does_not_fool_sniffer():
    text = 'name,notes\nAcme,"a;b;c;d"\nBeta,"x;y;z"\n'
    it = iter_rows(text.encode())
    list(it)
    assert it.summary.delimiter == ","


def test_excel_sep_hint_line():
    text = "sep=;\nname;website\nAcme;acme.example.com\n"
    it = iter_rows(text.encode())
    rows = list(it)
    assert it.summary.delimiter == ";"
    assert it.headers == ["name", "website"]
    assert rows[0].row_number == 1


def test_explicit_delimiter_and_bad_delimiter():
    rows = rows_of(b"name;x\nA;b\n", delimiter=";")
    assert rows[0].raw == {"name": "A", "x": "b"}
    with pytest.raises(ValueError, match="delimiter"):
        iter_rows(b"name\nA\n", delimiter="|")


def test_single_column_defaults_to_comma():
    it = iter_rows(b"name\nAcme\nBeta\n")
    assert [r.mapped["name"] for r in it] == ["Acme", "Beta"]
    assert it.summary.delimiter == ","


# ------------------------------------------------------------------ cleaning and numbering


def test_cell_cleaning_keeps_values_otherwise_verbatim():
    text = "name,website\n​  Acme‏ ﻿, acme.example.com \n"
    rows = rows_of(text.encode())
    assert rows[0].raw == {"name": "Acme", "website": "acme.example.com"}


def test_blank_rows_skipped_numbering_physical():
    text = "name\nA\n\n,\n  \nB\n\nC\n"
    it = iter_rows(text.encode())
    rows = list(it)
    assert [(r.row_number, r.raw["name"]) for r in rows] == [(1, "A"), (5, "B"), (7, "C")]
    s = it.summary
    assert (s.total_rows, s.blank_rows, s.error_rows, s.ok_rows) == (3, 4, 0, 3)


def test_leading_blank_lines_before_header():
    it = iter_rows(b"\n\n name , website \nA,a.example.com\n")
    rows = list(it)
    assert it.headers == ["name", "website"]
    assert rows[0].row_number == 1


def test_header_only_and_empty_and_whitespace_files():
    it = iter_rows(b"name,website\n")
    assert list(it) == []
    assert [w.code for w in it.summary.warnings] == [c.WARN_NO_DATA_ROWS]
    assert it.summary.complete
    cases = [(b"", c.FILE_EMPTY), (b"\n\n  \n", c.FILE_NO_HEADER), (b",,\n", c.FILE_NO_HEADER)]
    for data, code in cases:
        with pytest.raises(CsvFileError) as exc:
            iter_rows(data)
        assert exc.value.code == code


def test_ragged_rows_reported_not_fatal():
    text = (
        "name,website,country\nA,a.example.com,SA\nB,b.example.com\n"
        "C,c.example.com,SA,extra\nD,d.example.com,AE\n"
    )
    it = iter_rows(text.encode())
    rows = list(it)
    assert [r.ok for r in rows] == [True, False, False, True]
    assert codes(rows[1]) == [c.ROW_COLUMN_COUNT]
    assert rows[1].mapped == {}
    assert rows[1].raw == {"name": "B", "website": "b.example.com"}
    assert rows[3].row_number == 4
    assert it.summary.error_rows == 2
    assert it.summary.total_rows == 4


def test_duplicate_and_blank_headers_renamed_with_warning():
    it = iter_rows(b"name,Name,,name\nA,B,C,D\n")
    rows = list(it)
    assert it.headers == ["name", "Name", "column_3", "name (2)"]
    assert rows[0].raw == {"name": "A", "Name": "B", "column_3": "C", "name (2)": "D"}
    assert {w.code for w in it.summary.warnings} == {c.WARN_DUPLICATE_HEADERS, c.WARN_BLANK_HEADERS}
    assert rows[0].mapped == {"name": "A"}


# ------------------------------------------------------------------ limits and hostile input


class _Pipe:
    """Non-seekable binary stream."""

    def __init__(self, data):
        self._b = io.BytesIO(data)

    def read(self, n=-1):
        return self._b.read(n)

    def seekable(self):
        return False


def test_size_limit_bytes_seekable_and_stream():
    limits = CsvLimits(max_file_bytes=100)
    data = b"name\n" + b"x\n" * 100
    for source in (data, io.BytesIO(data), _Pipe(data)):
        with pytest.raises(CsvFileError) as exc:
            iter_rows(source, limits=limits)
        assert exc.value.code == c.FILE_TOO_LARGE
        assert "100" in str(exc.value)


def test_row_limit_5001_rows():
    body = "name\n" + "".join(f"Co {i}\n" for i in range(5001))
    seen: list[c.ParsedRow] = []
    with iter_rows(body.encode()) as it, pytest.raises(CsvFileError) as exc:
        seen.extend(it)
    assert exc.value.code == c.FILE_TOO_MANY_ROWS
    assert len(seen) == 5000
    assert not it.summary.complete
    with pytest.raises(CsvFileError):
        summarize(body.encode())
    ok = summarize(("name\n" + "".join(f"Co {i}\n" for i in range(5000))).encode())
    assert ok.total_rows == 5000
    assert ok.complete


def test_column_limit():
    header = ",".join(f"c{i}" for i in range(51))
    with pytest.raises(CsvFileError) as exc:
        iter_rows(header.encode())
    assert exc.value.code == c.FILE_TOO_MANY_COLUMNS
    fifty = ",".join(["name", *[f"c{i}" for i in range(49)]])
    assert len(iter_rows(fifty.encode()).headers) == 50


def test_cell_length_limit_row_error_and_header():
    big = "x" * 2001
    rows = rows_of(f"name,notes\nA,{big}\nB,ok\n".encode())
    assert codes(rows[0]) == [c.ROW_CELL_TOO_LONG]
    assert len(rows[0].raw["notes"]) == 2000
    assert rows[1].ok
    with pytest.raises(CsvFileError) as exc:
        iter_rows(f"name,{big}\n".encode())
    assert exc.value.code == c.FILE_HEADER_TOO_LONG


def test_huge_cell_beyond_csv_field_limit_is_row_error_and_parse_continues():
    huge = "y" * 200_000
    text = f'name,notes\nA,"{huge}"\nB,fine\n'
    it = iter_rows(text.encode())
    rows = list(it)
    assert rows[0].errors
    assert it.summary.complete


def test_nul_bytes_and_binary_signatures_rejected():
    cases = [
        (b"name\nAcme\x00corp\n", c.FILE_BINARY),
        (b"name\n" + b"a" * 100 + b"\x00\x01\x02", c.FILE_BINARY),
        (b"PK\x03\x04" + b"\x00" * 50, c.FILE_UNSUPPORTED),
        (b"%PDF-1.7\n", c.FILE_UNSUPPORTED),
        (b"\xd0\xcf\x11\xe0" + b"\x00" * 20, c.FILE_UNSUPPORTED),
    ]
    for data, code in cases:
        with pytest.raises(CsvFileError) as exc:
            iter_rows(data)
        assert exc.value.code == code
    # A NUL far into the file is caught by the full first pass.
    with pytest.raises(CsvFileError) as exc2:
        summarize(b"name\n" + b"A\n" * 50000 + b"B\x00\n")
    assert exc2.value.code == c.FILE_BINARY


def test_formula_payloads_kept_raw_and_exported_safely():
    payloads = ["=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "＝1+1"]
    text = "name,notes\n" + "".join(f'"{p}",x\n' for p in payloads if "\t" not in p)
    rows = rows_of(text.encode())
    assert rows[0].raw["name"] == "=cmd|' /C calc'!A0"
    for p in payloads:
        assert safe_cell(p).startswith("'")
    assert safe_cell("Acme") == "Acme"
    assert safe_cell(None) == ""
    assert safe_cell(5) == "5"
    assert safe_cell("  =1") == "'  =1"
    assert safe_cell("​=1") == "'​=1"
    assert safe_row(["=1", "ok", None]) == ["'=1", "ok", ""]


def test_non_binary_source_rejected():
    with pytest.raises(CsvFileError) as exc:
        iter_rows(io.StringIO("name\nA\n"))  # type: ignore[arg-type]
    assert exc.value.code == c.FILE_NOT_BINARY
    with pytest.raises(CsvFileError):
        iter_rows(12345)  # type: ignore[arg-type]


# ------------------------------------------------------------------ summary, sources


def test_summary_hash_size_and_sources(tmp_path: Path):
    data = codecs.BOM_UTF8 + f"company_name\n{ARABIC}\n".encode()
    expected = hashlib.sha256(data).hexdigest()
    path = tmp_path / "f.csv"
    path.write_bytes(data)
    for source in (data, bytearray(data), memoryview(data), io.BytesIO(data)):
        s = summarize(source)
        assert (s.file_sha256, s.file_size, s.complete) == (expected, len(data), True)
    with path.open("rb") as fh:
        assert summarize(fh).file_sha256 == expected
    # a stream positioned mid-file parses from where it is
    buf = io.BytesIO(b"junk" + data)
    buf.seek(4)
    assert summarize(buf).file_sha256 == expected


def test_non_seekable_stream_is_spooled():
    data = (HEADER + "".join(f"Co {i},c{i}.example.com,SA\n" for i in range(300))).encode()
    s = summarize(_Pipe(data))
    assert (s.total_rows, s.file_sha256) == (300, hashlib.sha256(data).hexdigest())


def test_early_stop_leaves_summary_incomplete_and_context_closes():
    with iter_rows(b"name\nA\nB\n") as it:
        first = next(it)
    assert first.row_number == 1
    it.close()
    it.close()
    assert not it.summary.complete


def test_preview_first_rows():
    text = "Company,URL,HQ Country\n" + "".join(f"Co {i},c{i}.example.com,uae\n" for i in range(30))
    p = preview(text.encode(), count=10)
    assert len(p.rows) == 10
    assert p.mapping.to_dict() == {"Company": "name", "URL": "website", "HQ Country": "country"}
    assert p.rows[0].mapped["country"] == "AE"
    assert p.summary.encoding == "utf-8"


# ------------------------------------------------------------------ streaming behaviour


class _Probe:
    """Generated file-like that records read sizes and refuses read-everything calls."""

    header = b"name,website\n"
    line = b"Company number %08d,host%08d.example.com\n"

    def __init__(self, rows: int):
        self.line_len = len(self.line % (0, 0))
        self.size = len(self.header) + rows * self.line_len
        self.pos = 0
        self.max_request = 0

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.size + offset
        return self.pos

    def read(self, n=-1):
        assert 0 < n <= 1024 * 1024, "reading the whole file at once"
        self.max_request = max(self.max_request, n)
        out = bytearray()
        while len(out) < n and self.pos < self.size:
            if self.pos < len(self.header):
                piece = self.header[self.pos :]
            else:
                idx, off = divmod(self.pos - len(self.header), self.line_len)
                piece = (self.line % (idx, idx))[off:]
            out += piece
            self.pos += len(piece)
        if len(out) > n:
            self.pos -= len(out) - n
            del out[n:]
        return bytes(out)


def test_streaming_constant_memory():
    limits = CsvLimits(max_file_bytes=500 * 1024 * 1024, max_rows=10_000_000)
    probe = _Probe(rows=20_000)  # about 1 MB, twice the memory ceiling asserted below
    tracemalloc.start()
    try:
        it = iter_rows(probe, limits=limits)
        count = sum(1 for _ in it)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == 20_000
    assert it.summary.total_rows == 20_000
    assert it.summary.file_size == probe.size
    assert peak < 512 * 1024, peak
    assert probe.max_request <= 64 * 1024


def test_first_row_does_not_read_the_whole_second_pass():
    probe = _Probe(rows=100_000)
    it = iter_rows(probe, limits=CsvLimits(max_rows=10**9, max_file_bytes=10**9))
    first_pass_end = probe.pos
    next(it)
    # the parsing pass has only pulled the first blocks, not the 5 MB file
    assert probe.pos < 200 * 1024 or first_pass_end == probe.pos
    assert it.summary.file_sha256 == ""
    it.close()


# ------------------------------------------------------------------ fuzz


def test_never_raises_on_arbitrary_bytes():
    rng = random.Random(61)
    seeds = [
        b"name,website\nAcme,a.example.com\n",
        codecs.BOM_UTF8 + "اسم الشركة;الموقع\nمثال;x.example.com\n".encode(),
        "name\tcountry\nA\tSA\n".encode("utf-16"),
        b'name,notes\n"a\nb","c""d"\n',
    ]
    inputs: list[bytes] = [rng.randbytes(rng.randint(0, 600)) for _ in range(400)]
    for seed in seeds:
        for _ in range(150):
            buf = bytearray(seed)
            for _ in range(rng.randint(1, 6)):
                op = rng.randint(0, 2)
                pos = rng.randint(0, len(buf))
                if op == 0 and buf:
                    buf[min(pos, len(buf) - 1)] = rng.randint(0, 255)
                elif op == 1:
                    buf[pos:pos] = rng.randbytes(rng.randint(1, 5))
                elif buf:
                    del buf[min(pos, len(buf) - 1)]
            inputs.append(bytes(buf))
    limits = CsvLimits(max_rows=50, max_file_bytes=4096, max_columns=10, max_cell_length=40)
    failures: list[str] = []
    for data in inputs:
        try:
            for row in iter_rows(data, limits=limits):
                assert row.row_number >= 1
                assert row.ok or row.mapped == {}
        except CsvFileError as exc:
            failures.append(exc.code)
    assert set(failures) <= set(c.MESSAGES)


# ------------------------------------------------------------------ mapping

ALIAS_CASES = [
    ("Company", "name"),
    ("Company Name", "name"),
    ("company_name", "name"),
    ("ORGANISATION", "name"),
    ("Organization", "name"),
    ("Name", "name"),
    ("Domain", "website"),
    ("URL", "website"),
    ("Website", "website"),
    ("Web Site", "website"),
    ("LinkedIn", "profile_url"),
    ("LinkedIn URL", "profile_url"),
    ("linkedin_url", "profile_url"),
    ("Profile URL", "profile_url"),
    ("HQ Country", "country"),
    ("Country", "country"),
    ("Country Code", "country"),
    ("اسم الشركة", "name"),
    ("اسم المنشأة", "name"),
    ("الموقع", "website"),
    ("الموقع الإلكتروني", "website"),
    ("الدولة", "country"),
    ("لينكدإن", "profile_url"),
    ("Compnay Name", "name"),
    ("Websit", "website"),
]


@pytest.mark.parametrize(("header", "target"), ALIAS_CASES)
def test_header_aliases(header, target):
    assert suggest_mapping([header]).columns == {header: target}


def test_suggest_mapping_full_and_unmapped():
    m = suggest_mapping(["Company", "Industry", "Website", "LinkedIn", "HQ Country", "Notes"])
    assert m.columns == {
        "Company": "name",
        "Website": "website",
        "LinkedIn": "profile_url",
        "HQ Country": "country",
    }
    assert m.unmapped == ("Industry", "Notes")
    assert m.fuzzy == ()
    assert m.header_for("website") == "Website"
    assert m.header_for("nope") is None


def test_exact_match_beats_fuzzy_and_no_double_mapping():
    m = suggest_mapping(["Websit", "Website", "Company", "Company Name"])
    assert m.columns["Website"] == "website"
    assert "Websit" not in m.columns
    assert m.columns["Company"] == "name"
    assert "Company Name" not in m.columns
    assert suggest_mapping(["Compnay Name"]).fuzzy == ("Compnay Name",)


def test_unrelated_headers_not_guessed():
    m = suggest_mapping(["Revenue", "Employees", "id", "x"])
    assert m.columns == {}
    assert m.unmapped == ("Revenue", "Employees", "id", "x")


def test_validate_mapping():
    headers = ["a", "b", "c"]
    assert validate_mapping(Mapping({"a": "name", "b": "website"}), headers) == []
    got = {
        i.code
        for i in validate_mapping(Mapping({"a": "website", "b": "website", "z": "name"}), headers)
    }
    assert got == {c.MAPPING_DUPLICATE_TARGET, c.MAPPING_UNKNOWN_COLUMN}
    missing = validate_mapping(Mapping({"a": "website"}), headers)
    assert [i.code for i in missing] == [c.NAME_COLUMN_MISSING]
    assert c.MAPPING_UNKNOWN_FIELD in {
        i.code for i in validate_mapping(Mapping({"a": "name", "b": "industry"}), headers)
    }
    assert Mapping.from_dict({"a": "name"}).to_dict() == {"a": "name"}
    assert missing[0].as_dict()["code"] == c.NAME_COLUMN_MISSING


def test_explicit_mapping_override_and_errors():
    data = b"Col A,Col B,Col C\nAcme,acme.example.com,Saudi Arabia\n"
    m = Mapping({"Col A": "name", "Col B": "website", "Col C": "country"})
    row = rows_of(data, mapping=m)[0]
    assert row.mapped == {"name": "Acme", "website": "acme.example.com", "country": "SA"}
    assert row.raw == {"Col A": "Acme", "Col B": "acme.example.com", "Col C": "Saudi Arabia"}
    with pytest.raises(MappingError) as exc:
        iter_rows(data)  # nothing recognised: name not mapped
    assert exc.value.code == c.NAME_COLUMN_MISSING
    with pytest.raises(MappingError) as exc2:
        iter_rows(data, mapping=Mapping({"nope": "name"}))
    assert {i.code for i in exc2.value.issues} == {c.MAPPING_UNKNOWN_COLUMN}
    assert isinstance(exc2.value, CsvFileError)
    assert exc2.value.as_issue().code == c.MAPPING_UNKNOWN_COLUMN


def test_apply_mapping_missing_cells_and_country_passthrough():
    m = Mapping({"n": "name", "c": "country"})
    assert apply_mapping({"n": "A"}, m) == {"name": "A", "country": ""}
    assert apply_mapping({"n": "A", "c": "Atlantis"}, m)["country"] == "Atlantis"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("sa", "SA"),
        (" SA ", "SA"),
        ("Saudi Arabia", "SA"),
        ("the United States", "US"),
        ("U.A.E.", "AE"),
        ("UK", "GB"),
        ("Türkiye", "TR"),
        ("السعودية", "SA"),
        ("الإمارات", "AE"),
        ("Atlantis", None),
        ("", None),
        ("S1", None),
    ],
)
def test_normalize_country(value, code):
    assert normalize_country(value) == code


def test_country_table_only_assigned_codes():
    assert set(c.COUNTRY_NAMES.values()) <= set(ISO_3166_ALPHA2)


def test_header_aliases_target_real_fields():
    assert set(c.HEADER_ALIASES) == set(c.FIELDS)


# ------------------------------------------------------------------ template


def test_template_bytes_and_sample_file_in_sync():
    tpl = build_template_csv()
    assert tpl.startswith(codecs.BOM_UTF8)
    assert tpl.decode("utf-8-sig") == "company_name,website,profile_url,country,industry,notes\r\n"
    it = iter_rows(tpl)
    assert list(it) == []
    assert it.mapping.to_dict() == {
        "company_name": "name",
        "website": "website",
        "profile_url": "profile_url",
        "country": "country",
    }
    sample = Path(__file__).resolve().parents[2] / "docs" / "samples" / "companies-template.csv"
    assert sample.read_bytes() == build_template_csv(include_examples=True)
    rows = rows_of(sample.read_bytes())
    assert len(rows) == 3
    assert all(r.ok for r in rows)
    assert rows[2].raw["company_name"] == ARABIC
    assert rows[1].mapped["country"] == "AE"
    assert rows[0].raw["industry"] == "Retail"
