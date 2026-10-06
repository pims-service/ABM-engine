"""Core models.

`BackgroundJob` is a deliberately minimal job-status record for the worker plumbing (issue #25).
The full Job and AuditLog models (ownership, payloads, results, history) are issue #44 and
replace this one; keep callers going through `apps.core.jobs` so that swap stays contained.
"""

from __future__ import annotations

import uuid

from django.db import models


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    RETRYING = "retrying", "Waiting to retry"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class BackgroundJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED, db_index=True
    )
    progress = models.PositiveSmallIntegerField(default=0, help_text="Percent, 0 to 100.")
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"
