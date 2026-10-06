"""Create the database cache table that carries Django-Q2 worker heartbeats to /readyz.

Django has no schema migration for cache tables (``createcachetable`` is a command), so run it
here: ``migrate`` is already part of every deploy, and the command skips an existing table.
"""

from django.conf import settings
from django.core.management import call_command
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def create_cache_table(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    call_command(
        "createcachetable",
        settings.Q_STATS_CACHE_TABLE,
        database=schema_editor.connection.alias,
        verbosity=0,
    )


def drop_cache_table(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    schema_editor.execute(
        f"DROP TABLE IF EXISTS {schema_editor.quote_name(settings.Q_STATS_CACHE_TABLE)}"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_background_job"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
