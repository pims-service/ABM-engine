"""Guard rails for configuration files.

* ``docs/environment.md`` is the single list of environment variables.
* Every ``.env.example`` must list exactly the variables the doc assigns to it.
* ``docker-compose.yml`` may only use documented variables.
* ``.env.example`` files may only hold placeholders, never something that looks like a real secret.

Pure file checks: no network, no database, safe in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "environment.md"
COMPOSE = REPO_ROOT / "docker-compose.yml"
EXAMPLE_FILES = {
    "root": REPO_ROOT / ".env.example",
    "backend": REPO_ROOT / "backend" / ".env.example",
    "frontend": REPO_ROOT / "frontend" / ".env.example",
}

pytestmark = pytest.mark.skipif(
    not DOC.exists(), reason="repository docs are not available (e.g. inside the backend image)"
)

ASSIGNMENT = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=(.*)$")
SECRET_NAME = re.compile(r"(SECRET|KEY|TOKEN|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)
PLACEHOLDER = re.compile(r"YOUR_[A-Z0-9_]+")
# Shapes of real credentials from common providers and generic private keys.
KNOWN_SECRET_SHAPES = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT
]
URL_PASSWORD = re.compile(r"://[^/\s:@]+:([^@\s/]+)@")


def documented_variables() -> dict[str, set[str]]:
    """Map variable name to the set of example files the doc says list it."""
    rows: dict[str, set[str]] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| `([A-Z][A-Z0-9_]*)` \|", line)
        if not match:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 7, f"{match.group(1)}: expected 7 columns, got {len(cells)}"
        files = {f.strip() for f in cells[5].split(",") if f.strip() != "-"}
        assert match.group(1) not in rows, f"{match.group(1)} is documented twice"
        rows[match.group(1)] = files
    return rows


def example_assignments(path: Path) -> list[tuple[str, str]]:
    """``(name, value)`` for each assignment in an example file, commented-out ones included."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ASSIGNMENT.match(line.strip())
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


def secret_findings(name: str, value: str) -> list[str]:
    """Reasons ``name=value`` looks like a real secret (empty list when it is fine)."""
    problems = [
        f"{name}: matches a known credential format"
        for shape in KNOWN_SECRET_SHAPES
        if shape.search(value)
    ]
    for password in URL_PASSWORD.findall(value):
        if not PLACEHOLDER.fullmatch(password):
            problems.append(f"{name}: URL contains a non-placeholder password")
    if SECRET_NAME.search(name) and value and not PLACEHOLDER.fullmatch(value):
        problems.append(f"{name}: secret-named variable must be empty or a YOUR_* placeholder")
    return problems


# ---------------------------------------------------------------- doc and examples


def test_doc_table_is_parsed() -> None:
    assert {"SECRET_KEY", "DATABASE_URL", "NEXT_PUBLIC_API_BASE_URL"} <= set(documented_variables())


@pytest.mark.parametrize("which", sorted(EXAMPLE_FILES))
def test_example_file_matches_documented_list(which: str) -> None:
    documented = documented_variables()
    expected = {name for name, files in documented.items() if which in files}
    actual = {name for name, _ in example_assignments(EXAMPLE_FILES[which])}
    assert actual - set(documented) == set(), f"{which}: variables missing from the doc"
    assert actual == expected, (
        f"{which} .env.example drifted from docs/environment.md: "
        f"only in example {sorted(actual - expected)}, only in doc {sorted(expected - actual)}"
    )


def test_documented_files_are_known_names() -> None:
    for name, files in documented_variables().items():
        assert files <= set(EXAMPLE_FILES), f"{name}: unknown example file in {sorted(files)}"


def test_compose_uses_only_documented_variables() -> None:
    documented = set(documented_variables())
    text = COMPOSE.read_text(encoding="utf-8")
    interpolated = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text))
    # Keys under an `environment:` mapping, e.g. "      SECRET_KEY: ${SECRET_KEY...}".
    keys = set(re.findall(r"^ {6}([A-Z][A-Z0-9_]*):", text, re.MULTILINE))
    assert (interpolated | keys) - documented == set()


def test_compose_required_variables_are_in_the_root_example() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", text))
    in_example = {name for name, _ in example_assignments(EXAMPLE_FILES["root"])}
    assert required <= in_example


# ---------------------------------------------------------------- placeholders only


@pytest.mark.parametrize("which", sorted(EXAMPLE_FILES))
def test_example_files_hold_placeholders_only(which: str) -> None:
    findings = [
        finding
        for name, value in example_assignments(EXAMPLE_FILES[which])
        for finding in secret_findings(name, value)
    ]
    assert findings == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_API_KEY", "sk-" + "a1B2c3D4" * 4),
        ("TOKEN", "ghp_" + "x" * 30),
        ("SECRET_KEY", "k7Hq" * 10),
        ("AWS_ID", "AKIA" + "ABCDEFGHIJKLMNOP"),
        ("DATABASE_URL", "postgres://app:realpassword@db:5432/app"),  # pragma: allowlist secret
        ("DEV_SUPERUSER_PASSWORD", "hunter2"),
        ("PRIVATE", "-----BEGIN RSA PRIVATE KEY-----"),
    ],
)
def test_checker_flags_realistic_secrets(name: str, value: str) -> None:
    assert secret_findings(name, value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SECRET_KEY", "YOUR_SECRET_KEY"),
        ("CRM_API_KEY", ""),
        ("DATABASE_URL", "postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@localhost:5432/YOUR_DB_NAME"),
        ("DEBUG", "True"),
        ("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000/api"),
        ("Q_TASK_TIMEOUT", "300"),
    ],
)
def test_checker_accepts_placeholders_and_plain_settings(name: str, value: str) -> None:
    assert secret_findings(name, value) == []
