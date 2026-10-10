"""Django admin for imports (issue #56). View only: batches are opened and moved by
``apps.imports.services`` and rows are append-only, so nobody gets add, change or delete buttons.

``raw_data`` is already scrubbed of credentials when stored. The row list is not an inline of the
batch because a CSV batch can hold thousands of rows: filter the row list by batch instead.
"""

from __future__ import annotations

from django.contrib import admin

from apps.core.admin_base import ReadOnlyAdmin

from .models import ImportBatch, ImportRow


@admin.register(ImportBatch)
class ImportBatchAdmin(ReadOnlyAdmin):
    list_display = (
        "created_at",
        "source",
        "status",
        "campaign",
        "client",
        "original_filename",
        "total_count",
        "created_count",
        "duplicate_count",
        "restored_count",
        "skipped_count",
        "failed_count",
    )
    list_filter = ("status", "source", "client")
    list_select_related = ("campaign", "client")
    search_fields = ("original_filename", "file_sha256", "campaign__name", "client__name")
    date_hierarchy = "created_at"


@admin.register(ImportRow)
class ImportRowAdmin(ReadOnlyAdmin):
    list_display = (
        "batch",
        "row_number",
        "outcome",
        "match_strength",
        "name",
        "domain",
        "error_code",
        "company",
    )
    list_filter = ("outcome", "match_strength", "client")
    list_select_related = ("batch", "company")
    search_fields = ("name", "domain", "error_code", "batch__original_filename")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "row_number")
