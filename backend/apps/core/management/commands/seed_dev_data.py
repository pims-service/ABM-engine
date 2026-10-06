"""Seed local development data. Safe to run repeatedly."""

from __future__ import annotations

import os
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.core.seeding import SEEDERS


class Command(BaseCommand):
    help = (
        "Create local development data (dev superuser from DEV_SUPERUSER_USERNAME / "
        "DEV_SUPERUSER_EMAIL / DEV_SUPERUSER_PASSWORD). Idempotent. Refuses to run when "
        "DEBUG is off unless --force is given."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even though DEBUG is off (never do this against production).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed data while DEBUG is off. Pass --force to override."
            )

        with transaction.atomic():
            for seeder in SEEDERS:
                result = seeder(os.environ)
                detail = f" ({result.detail})" if result.detail else ""
                self.stdout.write(
                    f"{result.name}: created={result.created} existing={result.existing}{detail}"
                )
        self.stdout.write(self.style.SUCCESS("Seed data is up to date."))
