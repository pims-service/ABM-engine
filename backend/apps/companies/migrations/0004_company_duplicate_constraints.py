"""Indexes and constraints for duplicate detection (issue #58), after the backfill of 0003."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0003_company_duplicate_detection"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="company",
            index=models.Index(fields=["campaign", "name_key"], name="co_company_camp_namekey_idx"),
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.UniqueConstraint(
                condition=models.Q(("profile_key__isnull", False)),
                fields=("campaign", "profile_key"),
                name="companies_company_profile_key_unique_per_campaign",
            ),
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.CheckConstraint(
                condition=models.Q(("possible_duplicate_of", models.F("id")), _negated=True),
                name="companies_company_not_duplicate_of_itself",
            ),
        ),
    ]
