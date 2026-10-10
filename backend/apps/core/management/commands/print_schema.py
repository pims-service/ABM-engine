"""Print or regenerate the generated parts of the data model docs (issue #54)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.core.schema_doc import BLOCKS, render_blocks, write_docs


class Command(BaseCommand):
    help = (
        "Print the ER diagram, entity index and field reference built from the models. "
        "With --write, update the generated blocks in docs/data-model.md and "
        "docs/data-model-reference.md (a test fails when they are out of date)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--write", action="store_true", help="Rewrite the docs in place.")
        parser.add_argument(
            "--block", choices=sorted(BLOCKS), help="Print only this block (default: all)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["write"]:
            changed = write_docs()
            for path in changed:
                self.stdout.write(f"updated {path}")
            self.stdout.write(
                self.style.SUCCESS("Docs are up to date." if not changed else "Done.")
            )
            return
        blocks = render_blocks()
        names = [options["block"]] if options["block"] else list(blocks)
        for name in names:
            self.stdout.write(f"<!-- {name} -->")
            self.stdout.write(blocks[name])
            self.stdout.write("")
