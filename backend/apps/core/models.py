"""Core models: Job, JobItem (background runs) and AuditLog (change tracking), issue #44.

Design: ``docs/data-model.md`` and ADR 0009. These replace the minimal ``BackgroundJob`` of
issue #25 behind the same ``apps.core.jobs`` helpers.

* ``Job``: one background run (a bulk analysis, a CSV import). Carries its client, optionally a
  campaign, counts and a status that can only follow the legal transitions below.
* ``JobItem``: one result row per subject (a company) in a job, so one failed item never fails
  the job. A job whose items are mixed ends ``partial``.
* ``AuditLog``: append-only record of who changed configuration (clients, campaigns) and what.

Change these rows only through ``apps.core.jobs`` (jobs) and ``apps.core.audit`` (audit log).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Any, ClassVar, Self

from django.conf import settings
from django.db import models
from django.utils import timezone

from .base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    BaseModel,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)


class InvalidTransitionError(ValueError):
    """A status change that the state machine does not allow (for example succeeded -> running)."""


def _check_transition(
    kind: str, old: str | None, new: str, table: dict[str, frozenset[str]]
) -> None:
    """Raise unless ``old -> new`` is a legal move (staying put, or an unknown old, is fine)."""
    if old is not None and new != old and new not in table.get(old, frozenset()):
        raise InvalidTransitionError(f"{kind} cannot go from {old} to {new}.")


def _in(field: str, choices: type[models.TextChoices]) -> models.Q:
    return models.Q(**{f"{field}__in": list(choices.values)})


# ------------------------------------------------------------------ Job


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    PARTIAL = "partial", "Partially succeeded"
    FAILED = "failed", "Failed"


JOB_TERMINAL = frozenset({JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED})

# running -> queued is a retry waiting for its backoff delay (``attempts`` counts the runs).
JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.QUEUED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.PARTIAL: frozenset(),
    JobStatus.FAILED: frozenset(),
}


class JobQuerySet(TenantQuerySet["Job"]):
    def active(self) -> Self:
        """Jobs that are queued or running."""
        return self.filter(status__in=[JobStatus.QUEUED, JobStatus.RUNNING])


class Job(TenantModel, BaseModel):
    """A background run. Its ``client`` is explicit; ``campaign`` (optional) must be the same
    client's. Create it with ``apps.core.jobs.create_job`` / ``enqueue`` and move it with the
    functions in that module: ``save`` refuses an illegal status change either way.

    Counts: ``total_count`` items to process, ``done_count`` succeeded, ``failed_count`` failed.
    ``done_count + failed_count <= total_count`` (database check).
    """

    type = models.CharField(
        max_length=100,
        help_text="A job type registered in code (`@tracked_job`), not free input.",
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="jobs",
    )
    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED)
    total_count = models.PositiveIntegerField(default=0)
    done_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0, help_text="Runs started so far.")
    error_summary = models.TextField(blank=True)
    queue_task_id = models.CharField(max_length=64, blank=True, help_text="Django-Q2 task id.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = JobQuerySet.as_manager()

    _loaded_status: str | None = None

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at",)
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["campaign", "-created_at"], name="core_job_campaign_created"),
            models.Index(fields=["status"], name="core_job_status"),
            models.Index(fields=["client", "-created_at"], name="core_job_client_created"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("status", JobStatus), name="core_job_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    done_count__lte=models.F("total_count") - models.F("failed_count")
                ),
                name="core_job_counts_within_total",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=sorted(s.value for s in JOB_TERMINAL), finished_at__isnull=False
                    )
                    | (
                        ~models.Q(status__in=sorted(s.value for s in JOB_TERMINAL))
                        & models.Q(finished_at__isnull=True)
                    )
                ),
                name="core_job_finished_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type} [{self.status}]"

    @property
    def is_finished(self) -> bool:
        return self.status in JOB_TERMINAL

    @property
    def processed_count(self) -> int:
        return self.done_count + self.failed_count

    @property
    def progress_percent(self) -> int:
        """0 to 100: processed items over total. A finished job with no items reads as 100."""
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
        if self._state.adding:
            self._check_campaign_client()
        else:
            _check_transition("Job", self._loaded_status, self.status, JOB_TRANSITIONS)
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    def _check_campaign_client(self) -> None:
        if not self.campaign_id:
            return
        from apps.campaigns.models import Campaign

        campaign_client = (
            Campaign.objects.filter(pk=self.campaign_id).values_list("client_id", flat=True).first()
        )
        if campaign_client != self.client_id:
            raise TenantMismatchError("Job.client_id does not match campaign.client_id.")


# ------------------------------------------------------------------ JobItem


class JobItemStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


ITEM_TERMINAL = frozenset({JobItemStatus.SUCCEEDED, JobItemStatus.FAILED})

ITEM_TRANSITIONS: dict[str, frozenset[str]] = {
    JobItemStatus.QUEUED: frozenset(
        {JobItemStatus.RUNNING, JobItemStatus.SUCCEEDED, JobItemStatus.FAILED}
    ),
    JobItemStatus.RUNNING: frozenset(
        {JobItemStatus.SUCCEEDED, JobItemStatus.FAILED, JobItemStatus.QUEUED}
    ),
    JobItemStatus.SUCCEEDED: frozenset(),
    JobItemStatus.FAILED: frozenset(),
}


class JobItemQuerySet(TenantQuerySet["JobItem"]):
    pass


class JobItem(TenantModel, BaseModel):
    """The result of one subject (usually a company) inside a job.

    ``subject_type`` / ``subject_id`` name the thing processed (``"company"`` and its id). It is
    a generic reference until the Company model exists (issue #40); one subject appears once per
    job. Items are terminal once succeeded or failed: to retry a failed subject, start a new job.
    """

    tenant_parent = "job"

    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="items")
    subject_type = models.CharField(max_length=64)
    subject_id = models.UUIDField()
    status = models.CharField(
        max_length=16, choices=JobItemStatus.choices, default=JobItemStatus.QUEUED
    )
    error = models.TextField(blank=True)
    result = models.JSONField(null=True, blank=True, help_text="Small JSON outcome, optional.")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = JobItemQuerySet.as_manager()

    _loaded_status: str | None = None

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("job_id", "created_at")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["job", "status"], name="core_jobitem_job_status"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["job", "subject_type", "subject_id"], name="core_jobitem_subject_unique"
            ),
            models.CheckConstraint(
                condition=_in("status", JobItemStatus), name="core_jobitem_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=sorted(s.value for s in ITEM_TERMINAL), finished_at__isnull=False
                    )
                    | (
                        ~models.Q(status__in=sorted(s.value for s in ITEM_TERMINAL))
                        & models.Q(finished_at__isnull=True)
                    )
                ),
                name="core_jobitem_finished_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.subject_type}:{self.subject_id} [{self.status}]"

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
        if not self._state.adding:
            _check_transition("Job item", self._loaded_status, self.status, ITEM_TRANSITIONS)
        super().save(*args, **kwargs)
        self._loaded_status = self.status


# ------------------------------------------------------------------ AuditLog


class AuditAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    ARCHIVE = "archive", "Archive"
    RESTORE = "restore", "Restore"
    DELETE = "delete", "Delete"


class AuditLogQuerySet(AppendOnlyQuerySet["AuditLog"], TenantQuerySet["AuditLog"]):  # type: ignore[override]
    def for_object(self, object_type: str, object_id: uuid.UUID | str) -> Self:
        return self.filter(object_type=object_type, object_id=object_id)


class AuditLog(AppendOnlyModel, UUIDModel):
    """Who changed what, and when (Brief section 15). Insert-only; never edited or deleted.

    Write entries with ``apps.core.audit.record`` / ``record_change``, which compute the diff and
    redact secrets. ``before`` / ``after`` hold only the fields that changed (a creation has
    ``before=None``, a deletion ``after=None``). ``client`` is set whenever the object belongs to
    a client and is what scopes the entry for a user; ``actor`` is null for the system.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    client = models.ForeignKey(
        "campaigns.Client",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=16, choices=AuditAction.choices)
    object_type = models.CharField(max_length=64)
    object_id = models.UUIDField()
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at",)
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["object_type", "object_id", "-created_at"], name="core_audit_object"
            ),
            models.Index(fields=["client", "-created_at"], name="core_audit_client"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("action", AuditAction), name="core_auditlog_action_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type} {self.object_id}"
