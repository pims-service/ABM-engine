"""Drop and recreate the local development database, then migrate it."""

from __future__ import annotations

from typing import Any

import psycopg
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from psycopg import sql

LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


class Command(BaseCommand):
    help = (
        "Drop and recreate the local PostgreSQL database named in DATABASE_URL, then run "
        "migrate. DESTROYS ALL DATA. Only works with DEBUG on and a local database host."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")
        parser.add_argument("--no-migrate", action="store_true", help="Only recreate the database.")

    def handle(self, *args: Any, **options: Any) -> None:
        db = settings.DATABASES["default"]
        if db["ENGINE"] != "django.db.backends.postgresql":
            raise CommandError("reset_local_db only supports PostgreSQL.")
        if not settings.DEBUG:
            raise CommandError("Refusing to reset the database while DEBUG is off.")
        if db.get("HOST", "") not in LOCAL_HOSTS:
            raise CommandError(f"Refusing to reset a non-local database host: {db['HOST']!r}.")

        name = db["NAME"]
        if not options["yes"]:
            prompt = f"This will DROP and recreate database '{name}'. Type its name to continue: "
            if input(prompt) != name:
                raise CommandError("Aborted.")

        connection.close()
        # Connect to the maintenance database: you cannot drop the one you are connected to.
        with psycopg.connect(
            dbname="postgres",
            user=db["USER"] or None,
            password=db["PASSWORD"] or None,
            host=db["HOST"] or None,
            port=db["PORT"] or None,
            autocommit=True,
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        self.stdout.write(f"Recreated database '{name}'.")

        if not options["no_migrate"]:
            call_command("migrate", verbosity=options["verbosity"])
