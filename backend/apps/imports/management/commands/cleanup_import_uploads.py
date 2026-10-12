"""Delete CSV upload files that are no longer needed (issue #62).

The worker deletes a file when its batch ends; this sweeps up what a crash left behind: files of
ended or unknown batches, and files older than ``--max-age-hours`` (default 24). Safe to run from
cron or by hand; it never touches files of younger pending or processing batches.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand

from apps.imports.uploads import purge_stale_uploads


class Command(BaseCommand):
    help = "Delete stale uploaded CSV files (ended or unknown batches, or older than the cut-off)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--max-age-hours", type=int, default=24)

    def handle(self, *args: Any, **options: Any) -> None:
        report = purge_stale_uploads(older_than=timedelta(hours=options["max_age_hours"]))
        self.stdout.write(f"Removed {report.removed} file(s); kept {report.kept}.")
