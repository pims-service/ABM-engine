"""Render the data model docs from the real models (issue #54).

``python manage.py print_schema`` prints, and ``--write`` rewrites, the generated blocks in
``docs/data-model.md`` (the ER diagram and the entity index) and ``docs/data-model-reference.md``
(every field, enum and constraint). ``tests/test_data_model_docs.py`` fails when those blocks no
longer match the models, so the doc cannot drift silently: run the command and commit the diff.

Only models defined in ``apps.*`` are described. Framework tables (auth groups, sessions, the
token blacklist, the task queue) are outside the data model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from django.apps import apps
from django.db import models

from .base import AppendOnlyModel, ArchivableModel, TenantModel

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs"
MODEL_DOC = DOCS / "data-model.md"
REFERENCE_DOC = DOCS / "data-model-reference.md"

#: Which generated blocks live in which file.
BLOCKS: dict[str, Path] = {"erd": MODEL_DOC, "index": MODEL_DOC, "reference": REFERENCE_DOC}


#: Foreign keys to User that only record authorship. They are in the field reference but left out
#: of the diagram, where they would add one line per table and hide the real structure.
AUTHORSHIP_FIELDS = frozenset({"created_by", "approved_by", "actor"})


def local_models() -> list[type[models.Model]]:
    """Concrete models defined by our own apps, in a stable order (app, then class name)."""
    found = [m for m in apps.get_models() if m.__module__.startswith("apps.")]
    return sorted(found, key=lambda m: (m._meta.app_label, m.__name__))


def kind(model: type[models.Model]) -> str:
    """How the model behaves: ``append-only``, ``mutable``, plus ``archivable`` / ``tenant``."""
    parts = ["append-only" if issubclass(model, AppendOnlyModel) else "mutable"]
    if issubclass(model, ArchivableModel):
        parts.append("archivable")
    if issubclass(model, TenantModel):
        parts.append("tenant")
    return ", ".join(parts)


def _type_label(field: models.Field) -> str:  # type: ignore[type-arg]
    if isinstance(field, models.ForeignKey):
        return _type_label(field.target_field)  # the type of the key it points at
    internal = field.get_internal_type()
    if internal in {"CharField", "EmailField", "URLField", "SlugField"}:
        return f"text({field.max_length})"
    if internal == "DecimalField":
        return f"numeric({field.max_digits},{field.decimal_places})"  # type: ignore[attr-defined]
    return {
        "UUIDField": "uuid",
        "TextField": "text",
        "BooleanField": "bool",
        "DateTimeField": "timestamptz",
        "DateField": "date",
        "JSONField": "jsonb",
        "StringListField": "text[]",
        "PositiveIntegerField": "int",
        "PositiveSmallIntegerField": "smallint",
        "IntegerField": "int",
    }.get(internal, internal)


def _fields(model: type[models.Model]) -> list[models.Field]:  # type: ignore[type-arg]
    return list(model._meta.concrete_fields)


def _field_notes(field: models.Field) -> str:  # type: ignore[type-arg]
    notes: list[str] = []
    if field.primary_key:
        notes.append("PK")
    if isinstance(field, models.ForeignKey):
        target = cast(Any, field.related_model)._meta.label
        notes.append(f"FK to {target}")
    if field.unique and not field.primary_key:
        notes.append("unique")
    if field.choices:
        notes.append("one of: " + ", ".join(f"`{v}`" for v, _ in field.choices))
    return "; ".join(notes)


def entity_name(model: type[models.Model]) -> str:
    return model.__name__


def mermaid_erd() -> str:
    """An ``erDiagram`` of every local model: relationships plus keys and enum columns."""
    lines = ["erDiagram"]
    relations: list[str] = []
    local = set(local_models())
    for model in local_models():
        for field in model._meta.concrete_fields:
            if not isinstance(field, models.ForeignKey):
                continue
            target = field.related_model
            if target is None or target not in local:
                continue  # e.g. a link to a framework table
            if field.name == "client" and issubclass(model, TenantModel):
                continue  # the tenancy column on every tenant table: said once in the doc
            if target._meta.label == "accounts.User" and field.name in AUTHORSHIP_FIELDS:
                continue  # who created or approved a row: said once in the doc
            left = "|o" if field.null else "||"
            right = "o|" if field.unique else "o{"
            relations.append(
                f'    {entity_name(target)} {left}--{right} {entity_name(model)} : "{field.name}"'
            )
    lines += sorted(relations)
    for model in local_models():
        lines.append(f"    {entity_name(model)} {{")
        for field in _fields(model):
            label = _type_label(field).replace("(", "_").replace(")", "").replace(",", "_")
            label = label.replace("[]", "_array")
            if field.primary_key:
                mark = "PK"
            elif isinstance(field, models.ForeignKey):
                mark = "FK"
            elif field.choices:
                mark = ""
            else:
                continue
            lines.append(f"        {label} {field.attname} {mark}".rstrip())
        lines.append("    }")
    return "\n".join(lines)


def entity_index() -> str:
    """A table of every local model with its app, behaviour and database table."""
    rows = ["| Model | App | Behaviour | Table |", "| --- | --- | --- | --- |"]
    for model in local_models():
        meta = model._meta
        rows.append(f"| {model.__name__} | {meta.app_label} | {kind(model)} | `{meta.db_table}` |")
    return "\n".join(rows)


def _plain(expression: object) -> str:
    """``Lower(F(name))`` as ``Lower(name)``: drop the ``F()`` wrapper around column names."""
    return re.sub(r"F\((\w+)\)", r"\1", str(expression))


def _constraint_line(constraint: models.BaseConstraint) -> str:
    if isinstance(constraint, models.UniqueConstraint):
        what = ", ".join(constraint.fields) or ", ".join(_plain(e) for e in constraint.expressions)
        partial = " (partial)" if constraint.condition is not None else ""
        return f"- `{constraint.name}`: unique ({what}){partial}"
    return f"- `{constraint.name}`: check"


def reference() -> str:
    """Every local model: its fields (type, null, notes), indexes and constraints."""
    out: list[str] = []
    for model in local_models():
        meta = model._meta
        out += [
            f"### {model.__name__}",
            "",
            f"`{meta.label}`, {kind(model)}, table `{meta.db_table}`.",
        ]
        out += ["", "| Field | Type | Null | Notes |", "| --- | --- | --- | --- |"]
        for field in _fields(model):
            null = "yes" if field.null else "no"
            out.append(
                f"| {field.attname} | {_type_label(field)} | {null} | {_field_notes(field)} |"
            )
        for m2m in meta.many_to_many:
            through_model = cast(Any, m2m.remote_field).through
            if through_model._meta.auto_created:
                continue
            through = through_model._meta.label
            out.append(f"| {m2m.name} | many to many | n/a | through `{through}` |")
        if meta.constraints:
            out += ["", "Constraints:", ""]
            out += [_constraint_line(c) for c in sorted(meta.constraints, key=lambda c: c.name)]
        if meta.indexes:
            out += ["", "Indexes:", ""]
            for index in sorted(meta.indexes, key=lambda i: i.name or ""):
                cols = ", ".join(index.fields) or ", ".join(_plain(e) for e in index.expressions)
                out.append(f"- `{index.name}`: ({cols})")
        out.append("")
    return "\n".join(out).rstrip()


def render_blocks() -> dict[str, str]:
    """The generated text of each block, keyed by block name."""
    return {
        "erd": "```mermaid\n" + mermaid_erd() + "\n```",
        "index": entity_index(),
        "reference": reference(),
    }


def _markers(name: str) -> tuple[str, str]:
    return (
        f"<!-- BEGIN GENERATED: {name} (python manage.py print_schema --write) -->",
        f"<!-- END GENERATED: {name} -->",
    )


def _block_pattern(name: str) -> re.Pattern[str]:
    begin, end = _markers(name)
    return re.compile(rf"{re.escape(begin)}\n(.*?)\n?{re.escape(end)}", re.DOTALL)


def extract_block(text: str, name: str) -> str | None:
    """The current content of a generated block in ``text``, or ``None`` if it is missing."""
    match = _block_pattern(name).search(text)
    return match.group(1) if match else None


def replace_block(text: str, name: str, content: str) -> str:
    begin, end = _markers(name)
    new = f"{begin}\n{content}\n{end}"
    if not _block_pattern(name).search(text):
        raise ValueError(f"Block '{name}' not found: add its BEGIN/END markers to the file.")
    return _block_pattern(name).sub(lambda _m: new, text, count=1)


def write_docs() -> list[Path]:
    """Rewrite the generated blocks in place. Returns the files that changed."""
    blocks = render_blocks()
    changed: list[Path] = []
    for path in dict.fromkeys(BLOCKS.values()):
        original = path.read_text(encoding="utf-8")
        text = original
        for name, target in BLOCKS.items():
            if target == path:
                text = replace_block(text, name, blocks[name])
        if text != original:
            path.write_text(text, encoding="utf-8", newline="\n")
            changed.append(path)
    return changed
