"""Factories for Job, JobItem and AuditLog (issue #44). Kept apart from `tests/factories.py`."""

from __future__ import annotations

import uuid
from typing import Any, cast

import factory

from apps.core.audit import record
from apps.core.models import AuditAction, AuditLog, Job, JobItem
from tests.factories import ClientFactory


class JobFactory(factory.django.DjangoModelFactory):
    """A queued job. Move it with `apps.core.jobs` functions; `status=` is only for fixtures that
    start from a state (the model still refuses illegal changes later, not at creation)."""

    class Meta:
        model = Job

    type = "ping"
    client = factory.SubFactory(ClientFactory)
    campaign = None
    created_by = None


class JobItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = JobItem

    job = factory.SubFactory(JobFactory)
    subject_type = "company"
    subject_id = factory.LazyFunction(uuid.uuid4)


class AuditLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuditLog

    action = AuditAction.UPDATE
    object_type = "client"
    object_id = factory.LazyFunction(uuid.uuid4)


def make_job(**overrides: Any) -> Job:
    return cast(Job, JobFactory(**overrides))


def make_job_item(**overrides: Any) -> JobItem:
    """Persisted item. Adding it directly does not change the job's counts: use
    `apps.core.jobs.add_items` when the totals matter."""
    return cast(JobItem, JobItemFactory(**overrides))


def make_audit_log(**overrides: Any) -> AuditLog:
    """Persisted entry, redacted like production ones (goes through `apps.core.audit.record`)."""
    params: dict[str, Any] = {
        "action": AuditAction.UPDATE,
        "object_type": "client",
        "object_id": uuid.uuid4(),
    }
    params.update(overrides)
    return record(**params)
