"""Factories for ImportBatch and ImportRow (issue #56). Kept apart from `tests/factories.py`."""

from __future__ import annotations

from typing import Any, cast

import factory

from apps.imports.models import ImportBatch, ImportRow, ImportRowOutcome
from tests.factories import CampaignFactory


class ImportBatchFactory(factory.django.DjangoModelFactory):
    """A pending manual batch straight through the model. Use `apps.imports.services` to move it
    and to record rows (those keep the counters right; the factory does not)."""

    class Meta:
        model = ImportBatch

    campaign = factory.SubFactory(CampaignFactory)
    source = "manual"
    total_count = 1


class ImportRowFactory(factory.django.DjangoModelFactory):
    """A skipped row (it needs no company). Pass `outcome="created", company=...` for others."""

    class Meta:
        model = ImportRow

    batch = factory.SubFactory(ImportBatchFactory)
    row_number = factory.Sequence(lambda n: n + 1)
    raw_data = factory.LazyFunction(lambda: {"name": "Acme"})
    name = "Acme"
    outcome = ImportRowOutcome.SKIPPED


def make_import_batch(**overrides: Any) -> ImportBatch:
    return cast(ImportBatch, ImportBatchFactory(**overrides))


def make_import_row(**overrides: Any) -> ImportRow:
    """Persisted row. Adding it directly does not change the batch's counters."""
    return cast(ImportRow, ImportRowFactory(**overrides))
