"""Re-encrypt every stored integration credential with the newest key (issue #76).

Rotation runbook (backend/README.md, "Integration credentials"):

1. Put a new key FIRST in ``INTEGRATION_ENCRYPTION_KEYS`` (keep the old ones after it), deploy.
2. ``python manage.py rotate_credentials``
3. When it reports no failures, remove the old key from the list and deploy.

Safe to re-run. A row that cannot be decrypted is left untouched and listed by id (never by
content); the command then exits with an error so a pipeline stops before step 3.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.integrations import crypto, services


class Command(BaseCommand):
    help = "Re-encrypt all integration credentials with the newest INTEGRATION_ENCRYPTION_KEYS key."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run", action="store_true", help="Check every row decrypts; change nothing."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]
        try:
            rotated, failed = services.rotate_all_credentials(dry_run=dry_run)
        except crypto.EncryptionConfigError as exc:
            raise CommandError(str(exc)) from None
        verb = "Would re-encrypt" if dry_run else "Re-encrypted"
        self.stdout.write(f"{verb} {rotated} credential set(s) with the newest key.")
        if failed:
            ids = ", ".join(str(pk) for pk in failed)
            raise CommandError(
                f"{len(failed)} row(s) could not be decrypted with the configured keys and were "
                f"left unchanged: {ids}. Keep the old key in INTEGRATION_ENCRYPTION_KEYS."
            )
