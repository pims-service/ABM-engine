"""Abstract building blocks every domain model reuses (data model ground rules, ADR 0009).

Pick the pieces a model needs:

==========================  ==================================================================
``BaseModel``               UUID primary key + ``created_at`` / ``updated_at``. Use for any
                            mutable table.
``ArchivableModel``         adds ``archived_at`` and ``archive()`` / ``restore()``. We archive,
                            we do not delete. Pair its manager with ``ArchivableQuerySet``.
``TenantModel``             adds the direct ``client`` FK (tenancy column) copied from the
                            parent row on save. Pair its manager with ``TenantQuerySet``.
``AppendOnlyModel``         rows are inserted and never updated or deleted by application code
                            (ADR 0007/0009). Pair its manager with ``AppendOnlyQuerySet``.
==========================  ==================================================================

A typical tenant, append-only model::

    class ThingQuerySet(  # type: ignore[override]
        AppendOnlyQuerySet["Thing"], TenantQuerySet["Thing"]
    ):
        pass

    class Thing(AppendOnlyModel, TenantModel, UUIDModel):
        tenant_parent = "company"          # FK name whose ``client_id`` is copied on create
        company = models.ForeignKey(Company, on_delete=models.PROTECT)
        objects = ThingQuerySet.as_manager()

Query rule: views and services load tenant data through ``Model.objects.for_user(user)`` (or
``for_client(client)`` inside jobs), never a bare ``Model.objects.all()``.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Any, ClassVar, Self, TypeVar

from django.db import models
from django.utils import timezone

from apps.core.tenancy import accessible_client_ids

_M = TypeVar("_M", bound=models.Model)


class ImmutableRecordError(Exception):
    """An append-only row was updated or deleted. Insert a new row instead."""


class TenantMismatchError(ValueError):
    """A row's ``client_id`` disagrees with its parent's, or it was moved to another client."""


# ------------------------------------------------------------------ keys and timestamps


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimestampedModel):
    """UUID primary key plus ``created_at`` and ``updated_at``."""

    class Meta:
        abstract = True


# ------------------------------------------------------------------ archive


class ArchivableQuerySet(models.QuerySet[_M]):
    def active(self) -> Self:
        """Rows that are not archived (what default lists show)."""
        return self.filter(archived_at__isnull=True)

    def archived(self) -> Self:
        return self.filter(archived_at__isnull=False)


class ArchivableModel(models.Model):
    """Soft delete. Foreign keys are PROTECT, so rows are hidden with ``archived_at``, not removed.

    If the model has a ``status`` column, set ``archived_status`` and ``restored_status`` so the
    two stay in step (add a CheckConstraint tying them together). Archiving never cascades to
    children: they are hidden because their parent is archived.
    """

    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    archived_status: ClassVar[str | None] = None
    restored_status: ClassVar[str | None] = None

    class Meta:
        abstract = True

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self) -> None:
        """Idempotent: archiving an archived row keeps the original timestamp."""
        if self.is_archived:
            return
        self.archived_at = timezone.now()
        self._save_archive_state()

    def restore(self) -> None:
        if not self.is_archived:
            return
        self.archived_at = None
        self._save_archive_state()

    def _save_archive_state(self) -> None:
        fields = ["archived_at"]
        new_status = self.restored_status if self.archived_at is None else self.archived_status
        if new_status is not None:
            setattr(self, "status", new_status)  # noqa: B010 - status is declared by the subclass
            fields.append("status")
        if hasattr(self, "updated_at"):
            fields.append("updated_at")
        self.save(update_fields=fields)


# ------------------------------------------------------------------ tenancy


class TenantQuerySet(models.QuerySet[_M]):
    """Scopes rows to the clients a user may see (ADR 0009, data model "Tenancy")."""

    #: Lookup that holds the owning client id. The ``Client`` table itself overrides it to ``id``.
    tenant_lookup: ClassVar[str] = "client_id"

    def for_user(self, user: Any) -> Self:
        """Rows in clients ``user`` may see. Global admins see all; anyone else sees only their
        clients; anonymous or inactive users see nothing (fails closed)."""
        client_ids = accessible_client_ids(user)
        if client_ids is None:
            return self.all()
        return self.filter(**{f"{self.tenant_lookup}__in": client_ids})

    def for_client(self, client: Any) -> Self:
        """Rows of one client, given a ``Client`` or its id. For jobs that carry a client."""
        client_id = getattr(client, "pk", client)
        return self.filter(**{self.tenant_lookup: client_id})


class TenantModel(models.Model):
    """Every tenant-owned table carries a direct ``client`` FK (the tenancy column).

    Callers never type ``client`` for child rows: set ``tenant_parent`` to the name of the FK
    whose row has the ``client_id`` and ``save()`` copies it (and refuses a mismatch). A root
    row (one directly under a client, like ``Campaign``) leaves ``tenant_parent`` empty and is
    given its client explicitly. A row cannot be moved to another client once saved.
    """

    client = models.ForeignKey(
        "campaigns.Client",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
        related_query_name="%(app_label)s_%(class)s",
    )

    tenant_parent: ClassVar[str | None] = None
    _loaded_client_id: uuid.UUID | None = None

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.sync_client()
        super().save(*args, **kwargs)
        self._loaded_client_id = self.client_id

    @classmethod
    def from_db(
        cls, db: str | None, field_names: Collection[str], values: Collection[Any], **kwargs: Any
    ) -> Self:
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._loaded_client_id = instance.__dict__.get("client_id")
        return instance

    def sync_client(self) -> None:
        """Copy ``client_id`` from the parent, and enforce it never disagrees or changes."""
        if self.tenant_parent:
            parent_client_id = getattr(self, self.tenant_parent).client_id
            if not self.client_id:  # not copied yet
                self.client_id = parent_client_id
            elif self.client_id != parent_client_id:
                raise TenantMismatchError(
                    f"{type(self).__name__}.client_id {self.client_id} does not match "
                    f"{self.tenant_parent}.client_id {parent_client_id}."
                )
        if (
            not self._state.adding
            and self._loaded_client_id is not None
            and self.client_id != self._loaded_client_id
        ):
            raise TenantMismatchError(f"{type(self).__name__} cannot move to another client.")


# ------------------------------------------------------------------ append-only


class AppendOnlyQuerySet(models.QuerySet[_M]):
    """Blocks bulk UPDATE and DELETE so history cannot be rewritten through the ORM."""

    def update(self, **kwargs: Any) -> int:
        raise ImmutableRecordError(f"{self.model.__name__} rows are append-only.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise ImmutableRecordError(f"{self.model.__name__} rows are append-only.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ImmutableRecordError(f"{self.model.__name__} rows are append-only.")


class AppendOnlyModel(models.Model):
    """Inserted once, never updated or deleted by application code. Corrections are new rows.

    This is an application-level guard (``save``, ``delete`` and the queryset methods raise
    ``ImmutableRecordError``). Raw SQL and the database shell are out of its reach.
    """

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError(
                f"{type(self).__name__} is append-only; create a new row instead of saving."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ImmutableRecordError(f"{type(self).__name__} is append-only and cannot be deleted.")
