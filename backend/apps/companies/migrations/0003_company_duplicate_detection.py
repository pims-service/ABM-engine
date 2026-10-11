"""Company duplicate detection columns (issue #58): ``profile_key``, ``name_key`` and
``possible_duplicate_of``, with a backfill of the derived keys for existing rows.

The unique index on ``(campaign, profile_key)`` is added in the next migration, after the
backfill, so that this one never fails on data that already holds two rows with one profile.
"""

from typing import Any

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.companies.normalize import name_match_key, profile_key

BATCH = 500


def backfill_keys(apps: Any, schema_editor: Any) -> None:
    """Fill ``name_key`` and ``profile_key`` from ``name`` and ``profile_url``.

    Existing data is never edited otherwise. If two rows of one campaign already share a
    profile identity (possible: nothing forbade it before), the oldest keeps the key and the
    later ones keep ``profile_key`` null, so the unique index can be created; their
    ``profile_url`` stays as it was.
    """
    Company = apps.get_model("companies", "Company")
    seen: set[tuple[object, str]] = set()
    pending: list[Any] = []
    rows = Company.objects.order_by("created_at", "id").iterator(chunk_size=BATCH)
    for company in rows:
        key = profile_key(company.profile_url)
        if key is not None:
            marker = (company.campaign_id, key)
            if marker in seen:
                key = None
            else:
                seen.add(marker)
        company.profile_key = key
        company.name_key = name_match_key(company.name)
        pending.append(company)
        if len(pending) >= BATCH:
            Company.objects.bulk_update(pending, ["profile_key", "name_key"])
            pending = []
    if pending:
        Company.objects.bulk_update(pending, ["profile_key", "name_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("campaigns", "0003_client_membership"),
        ("companies", "0002_contact"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="name_key",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Normalized name used for the weak duplicate match.",
                max_length=300,
            ),
        ),
        migrations.AddField(
            model_name="company",
            name="possible_duplicate_of",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                help_text="Set once at creation when a weak (name and country) match was found.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="possible_duplicates",
                to="companies.company",
            ),
        ),
        migrations.AddField(
            model_name="company",
            name="profile_key",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text="Canonical profile identity (linkedin:acme), or null.",
                max_length=520,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_keys, migrations.RunPython.noop),
    ]
