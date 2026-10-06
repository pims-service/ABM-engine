"""Install the PostgreSQL extensions the project relies on.

``pgcrypto`` provides ``gen_random_uuid()`` and hashing helpers; ``pg_trgm`` provides trigram
indexes for fuzzy company-name matching. Both operations are no-ops on non-PostgreSQL
backends (the SQLite test path), so this migration is safe everywhere.
"""

from django.contrib.postgres.operations import CryptoExtension, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        CryptoExtension(),
        TrigramExtension(),
    ]
