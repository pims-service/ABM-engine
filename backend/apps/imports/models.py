"""ImportBatch and ImportRow: one place for every way companies come in (issue #56).

Design: ``docs/data-model.md`` (ImportBatch, ImportRow) and ADR 0009. Manual entry, CSV upload
and provider import all open a batch, record one row per submitted company with what happened to
it, and finish the batch. Brief section 19 asks for history: the rows keep what was submitted.

* ``ImportBatch`` is mutable working state while it runs: ``status``, the counters,
  ``started_at`` / ``finished_at``, ``error_summary``, ``job`` and ``column_mapping``. Everything
  else (campaign, source, file facts, creator) is fixed at creation. Move it only through
  ``apps.imports.services``; ``save`` refuses an illegal status change either way.
* ``ImportRow`` is append-only: written once, with its outcome, by ``record_row_outcome``. A
  re-run of a row (a redelivered task) finds the row already there and changes nothing; a
  retry of a failed row is a new batch. ``raw_data`` is scrubbed of credentials and size-capped
  on save (``apps.imports.rawdata``).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, ClassVar, Self

from django.conf import settings
from django.db import models

from apps.companies.dedupe import MatchStrength
from apps.companies.models import InputSource
from apps.core.base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    BaseModel,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)
from apps.core.models import InvalidTransitionError

from .rawdata import sanitize_raw_data

#: How a batch was fed. Same values as ``Company.input_source`` (one vocabulary).
ImportSource = InputSource


def _in(field: str, choices: type[models.TextChoices]) -> models.Q:
    return models.Q(**{f"{field}__in": list(choices.values)})


# ------------------------------------------------------------------ ImportBatch


class ImportBatchStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Partially completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


BATCH_TERMINAL = frozenset(
    {
        ImportBatchStatus.COMPLETED,
        ImportBatchStatus.PARTIAL,
        ImportBatchStatus.FAILED,
        ImportBatchStatus.CANCELLED,
    }
)

BATCH_TRANSITIONS: dict[str, frozenset[str]] = {
    ImportBatchStatus.PENDING: frozenset(
        {ImportBatchStatus.PROCESSING, ImportBatchStatus.FAILED, ImportBatchStatus.CANCELLED}
    ),
    ImportBatchStatus.PROCESSING: frozenset(
        {
            ImportBatchStatus.COMPLETED,
            ImportBatchStatus.PARTIAL,
            ImportBatchStatus.FAILED,
            ImportBatchStatus.CANCELLED,
        }
    ),
    ImportBatchStatus.COMPLETED: frozenset(),
    ImportBatchStatus.PARTIAL: frozenset(),
    ImportBatchStatus.FAILED: frozenset(),
    ImportBatchStatus.CANCELLED: frozenset(),
}


class ImportBatchQuerySet(TenantQuerySet["ImportBatch"]):
    def active(self) -> Self:
        """Batches that are pending or processing."""
        return self.filter(status__in=[ImportBatchStatus.PENDING, ImportBatchStatus.PROCESSING])


class ImportBatch(TenantModel, BaseModel):
    """One submission of companies into a campaign: a manual entry (one row), a CSV upload or a
    provider import. ``client`` is copied from the campaign.

    Counters: ``total_count`` rows expected (set when known, for a CSV after parsing) and one
    counter per outcome. The outcomes are disjoint: a duplicate that restored an archived company
    is counted in ``restored_count`` only. ``processed_count`` is their sum and may never exceed
    ``total_count`` (database check). Status ends ``completed`` (no failed rows), ``partial``
    (some failed, some did not), ``failed`` (every row failed, or the run itself broke) or
    ``cancelled``. ``job`` links the background run that processes the rows so a screen can show
    its progress; the batch counters are the source of truth for the outcome.

    ``file_sha256`` identifies the uploaded CSV (the file itself is not kept); ``column_mapping``
    is free-form JSON saying which column fed which company field.
    """

    tenant_parent = "campaign"

    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, related_name="import_batches"
    )
    source = models.CharField(max_length=16, choices=ImportSource.choices)
    status = models.CharField(
        max_length=16, choices=ImportBatchStatus.choices, default=ImportBatchStatus.PENDING
    )
    original_filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(null=True, blank=True, help_text="Bytes.")
    file_sha256 = models.CharField(max_length=64, blank=True, help_text="Hex digest, CSV only.")
    column_mapping = models.JSONField(default=dict, blank=True)

    total_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    restored_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    error_summary = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    job = models.ForeignKey(
        "core.Job",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="import_batches",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = ImportBatchQuerySet.as_manager()

    _loaded_status: str | None = None

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        verbose_name_plural = "import batches"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["campaign", "-created_at"], name="imp_batch_campaign_created"),
            models.Index(fields=["client", "-created_at"], name="imp_batch_client_created"),
            models.Index(fields=["status"], name="imp_batch_status"),
            models.Index(fields=["campaign", "file_sha256"], name="imp_batch_campaign_sha"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("status", ImportBatchStatus), name="imports_batch_status_valid"
            ),
            models.CheckConstraint(
                condition=_in("source", ImportSource), name="imports_batch_source_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    total_count__gte=models.F("created_count")
                    + models.F("duplicate_count")
                    + models.F("restored_count")
                    + models.F("skipped_count")
                    + models.F("failed_count")
                ),
                name="imports_batch_counts_within_total",
            ),
            models.CheckConstraint(
                condition=~models.Q(source=ImportSource.CSV) | ~models.Q(file_sha256=""),
                name="imports_batch_csv_has_sha256",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=sorted(s.value for s in BATCH_TERMINAL),
                        finished_at__isnull=False,
                    )
                    | (
                        ~models.Q(status__in=sorted(s.value for s in BATCH_TERMINAL))
                        & models.Q(finished_at__isnull=True)
                    )
                ),
                name="imports_batch_finished_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source} import [{self.status}]"

    @property
    def is_finished(self) -> bool:
        return self.status in BATCH_TERMINAL

    @property
    def processed_count(self) -> int:
        return (
            self.created_count
            + self.duplicate_count
            + self.restored_count
            + self.skipped_count
            + self.failed_count
        )

    @property
    def progress_percent(self) -> int:
        """0 to 100: processed rows over total. A finished batch with no rows reads as 100."""
        if self.total_count:
            return min(100, self.processed_count * 100 // self.total_count)
        return 100 if self.is_finished else 0

    @classmethod
    def from_db(
        cls, db: str | None, field_names: Collection[str], values: Collection[Any], **kwargs: Any
    ) -> Self:
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._loaded_status = instance.__dict__.get("status")
        return instance

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._loaded_status = self.status

    def save(self, *args: Any, **kwargs: Any) -> None:
        old = self._loaded_status
        if self._state.adding:
            self.check_job()
        elif (
            old is not None
            and self.status != old
            and self.status not in BATCH_TRANSITIONS.get(old, frozenset())
        ):
            raise InvalidTransitionError(f"Import batch cannot go from {old} to {self.status}.")
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    def check_job(self) -> None:
        """The linked job must belong to the same client (and campaign, when it has one)."""
        job = self.job
        if job is None:
            return
        if job.client_id != self.campaign.client_id:
            raise TenantMismatchError("ImportBatch.job belongs to a different client.")
        if job.campaign_id is not None and job.campaign_id != self.campaign_id:
            raise TenantMismatchError("ImportBatch.job belongs to a different campaign.")


# ------------------------------------------------------------------ ImportRow


class ImportRowOutcome(models.TextChoices):
    CREATED = "created", "Created"
    DUPLICATE = "duplicate", "Duplicate"
    RESTORED = "restored", "Restored"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


#: Outcomes that point at a company (the new one, or the one that was already there).
ROW_COMPANY_OUTCOMES = frozenset(
    {ImportRowOutcome.CREATED, ImportRowOutcome.DUPLICATE, ImportRowOutcome.RESTORED}
)

#: ``ImportBatch`` counter that each outcome adds to.
OUTCOME_COUNTERS: dict[str, str] = {
    ImportRowOutcome.CREATED: "created_count",
    ImportRowOutcome.DUPLICATE: "duplicate_count",
    ImportRowOutcome.RESTORED: "restored_count",
    ImportRowOutcome.SKIPPED: "skipped_count",
    ImportRowOutcome.FAILED: "failed_count",
}


#: ``ImportRow.match_strength`` values (the ``none`` strength is stored as blank).
ROW_MATCH_STRENGTHS = [(MatchStrength.STRONG.value, "Strong"), (MatchStrength.WEAK.value, "Weak")]


class ImportRowQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["ImportRow"], TenantQuerySet["ImportRow"]
):
    pass


class ImportRow(AppendOnlyModel, TenantModel, UUIDModel):
    """What happened to one submitted company. Append-only (written once with its outcome).

    ``row_number`` is the 1-based position in the submission (the CSV data row, not counting the
    header); unique per batch. ``raw_data`` is what was submitted, scrubbed of credentials and
    size-capped on save. The normalized fields are what validation made of it (blank when the row
    was too broken to normalize). ``company`` is the created company, or the existing one for a
    duplicate or a restore; it is null for skipped and failed rows. ``match_strength``,
    ``matched_on`` and ``candidate`` record the duplicate match (issue #58): strong for a
    duplicate or restored row (``candidate`` is that existing company, same as ``company``), weak
    for a created row flagged as a possible duplicate (``candidate`` is the other company); all
    three are blank/null when nothing matched. A failed row carries a stable
    ``error_code`` (see ``apps.imports.schema`` and ``services``) and a human ``error_message``.
    """

    tenant_parent = "batch"

    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict, blank=True)
    name = models.CharField(max_length=300, blank=True)
    website = models.CharField(max_length=2000, blank=True)
    domain = models.CharField(max_length=253, null=True, blank=True)  # noqa: DJ001 - null: no domain
    profile_url = models.CharField(max_length=2000, blank=True)
    country = models.CharField(max_length=2, blank=True)
    outcome = models.CharField(max_length=16, choices=ImportRowOutcome.choices)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    company = models.ForeignKey(
        "companies.Company",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="import_rows",
    )
    match_strength = models.CharField(
        max_length=8,
        blank=True,
        choices=ROW_MATCH_STRENGTHS,
        help_text="strong: linked to an existing company; weak: created and flagged.",
    )
    matched_on = models.CharField(
        max_length=16, blank=True, help_text="domain, profile or name_country."
    )
    candidate = models.ForeignKey(
        "companies.Company",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="The matched company: the existing one (strong) or the possible duplicate.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImportRowQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("batch_id", "row_number")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["batch", "outcome", "row_number"], name="imp_row_batch_outcome"),
            models.Index(fields=["company"], name="imp_row_company"),
            models.Index(fields=["client"], name="imp_row_client"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["batch", "row_number"], name="imports_row_number_unique_per_batch"
            ),
            models.CheckConstraint(
                condition=_in("outcome", ImportRowOutcome), name="imports_row_outcome_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(row_number__gte=1), name="imports_row_number_positive"
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(outcome__in=sorted(o.value for o in ROW_COMPANY_OUTCOMES))
                    | models.Q(company__isnull=False)
                ),
                name="imports_row_company_for_outcome",
            ),
            models.CheckConstraint(
                condition=~models.Q(outcome=ImportRowOutcome.FAILED) | ~models.Q(error_code=""),
                name="imports_row_failed_has_code",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(match_strength="", matched_on="", candidate__isnull=True)
                    | (
                        models.Q(match_strength__in=[s for s, _ in ROW_MATCH_STRENGTHS])
                        & ~models.Q(matched_on="")
                        & models.Q(candidate__isnull=False)
                    )
                ),
                name="imports_row_match_complete",
            ),
        ]

    def __str__(self) -> str:
        return f"row {self.row_number} [{self.outcome}]"

    def sync_client(self) -> None:
        super().sync_client()
        for label, company in (("company", self.company), ("candidate", self.candidate)):
            if company is None:
                continue
            if company.client_id != self.client_id:
                raise TenantMismatchError(f"ImportRow.{label} belongs to a different client.")
            if company.campaign_id != self.batch.campaign_id:
                raise TenantMismatchError(f"ImportRow.{label} belongs to a different campaign.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding:
            self.raw_data = sanitize_raw_data(self.raw_data)
        super().save(*args, **kwargs)
