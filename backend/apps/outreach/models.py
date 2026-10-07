"""Outreach angle, message and activity (issue #43, Brief sections 9, 12, 14, 16).

Design: ``docs/data-model.md`` ("OutreachAngle", "Message", "Activity") and ADR 0007 / 0009.

* ``OutreachAngle`` (append-only) is *why* we contact a company. It is stored apart from the
  messages so one angle feeds many messages (LinkedIn, email, WhatsApp, call). Evidence is cited
  through two M2M tables (``AngleSignal``, ``AngleSource``), never a JSON list.
* ``Message`` keeps its text immutable. Only workflow state moves, and only forward
  (``draft`` -> ``approved`` -> ``exported``). An edit is a new row whose ``supersedes`` points at
  the old one. Messages cite the evidence they use (``MessageSignal``, ``MessageSource``) so
  nothing in a message is unsupported personalization (Brief section 22).
* ``Activity`` (append-only) is the per-company timeline (Brief section 14) and also the home of
  Brief section 16 feedback (``type=feedback``, payload validated in ``services``).
* No ``is_current`` column anywhere: use the manager methods (``latest_for``, ``current``).
* Create rows through ``apps.outreach.services``. It enforces the rules the database cannot
  (grounding, same-client references, the human ``add`` decision).
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.companies.models import Company, Contact, DataSource
from apps.core.base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    ImmutableRecordError,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)
from apps.research.models import Signal


def _in(field: str, choices: type[models.TextChoices]) -> models.Q:
    return models.Q(**{f"{field}__in": list(choices.values)})


def _user_fk() -> models.ForeignKey[Any, Any]:
    return models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )


# ------------------------------------------------------------------ OutreachAngle


class OutreachAngleQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["OutreachAngle"], TenantQuerySet["OutreachAngle"]
):
    def latest_first(self) -> OutreachAngleQuerySet:
        return self.order_by("-created_at", "-id")

    def for_company(self, company: Company | Any) -> OutreachAngleQuerySet:
        return self.filter(company=company)

    def latest_for(self, company: Company | Any) -> OutreachAngle | None:
        """The current angle of one company (latest ``created_at``, ties by id), or ``None``."""
        return self.for_company(company).latest_first().first()


class OutreachAngle(AppendOnlyModel, TenantModel, UUIDModel):
    """Why we should contact this account (Brief section 9). Immutable once saved."""

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="outreach_angles")
    angle = models.TextField()
    rationale = models.TextField(help_text='The "because..." shown to the reviewer.')
    signals = models.ManyToManyField(
        Signal, through="AngleSignal", related_name="outreach_angles", blank=True
    )
    data_sources = models.ManyToManyField(
        DataSource, through="AngleSource", related_name="outreach_angles", blank=True
    )
    model_name = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    schema_version = models.CharField(max_length=50, blank=True)
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OutreachAngleQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["company", "-created_at"], name="out_angle_company_idx"),
            models.Index(fields=["client"], name="out_angle_client_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~models.Q(angle=""), name="outreach_angle_text_not_empty"
            ),
            models.CheckConstraint(
                condition=~models.Q(rationale=""), name="outreach_angle_rationale_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return self.angle[:60]


class EvidenceLinkQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["_EvidenceLink"], TenantQuerySet["_EvidenceLink"]
):
    pass


class _EvidenceLink(AppendOnlyModel, TenantModel, UUIDModel):
    """Shared by the four M2M through tables. Rows are inserted by services, never edited.

    The manager blocks bulk update and delete like every other append-only table, and scopes
    rows with ``for_user`` / ``for_client``.
    """

    objects = EvidenceLinkQuerySet.as_manager()

    class Meta:
        abstract = True

    def _check_target(self, signal: Signal | None, source: DataSource | None, owner: Any) -> None:
        if signal is not None and signal.company_id != owner.company_id:
            raise TenantMismatchError(
                f"{type(self).__name__}: signal belongs to a different company."
            )
        if source is not None and source.client_id != owner.client_id:
            raise TenantMismatchError(
                f"{type(self).__name__}: data source belongs to a different client."
            )


class AngleSignal(_EvidenceLink):
    tenant_parent = "angle"

    angle = models.ForeignKey(OutreachAngle, on_delete=models.PROTECT, related_name="signal_links")
    signal = models.ForeignKey(Signal, on_delete=models.PROTECT, related_name="angle_links")

    class Meta:
        db_table = "outreach_angle_signals"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["angle", "signal"], name="outreach_anglesignal_unique")
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["signal"], name="out_anglesig_signal_idx")
        ]

    def sync_client(self) -> None:
        super().sync_client()
        self._check_target(self.signal, None, self.angle)


class AngleSource(_EvidenceLink):
    tenant_parent = "angle"

    angle = models.ForeignKey(OutreachAngle, on_delete=models.PROTECT, related_name="source_links")
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="angle_links"
    )

    class Meta:
        db_table = "outreach_angle_sources"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["angle", "data_source"], name="outreach_anglesource_unique"
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["data_source"], name="out_anglesrc_source_idx")
        ]

    def sync_client(self) -> None:
        super().sync_client()
        self._check_target(None, self.data_source, self.angle)


# ------------------------------------------------------------------ Message


class Channel(models.TextChoices):
    LINKEDIN = "linkedin", "LinkedIn"
    EMAIL = "email", "Email"
    WHATSAPP = "whatsapp", "WhatsApp"
    CALL = "call", "Call"


class MessageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    EXPORTED = "exported", "Exported"


_STATUS_RANK = {MessageStatus.DRAFT: 0, MessageStatus.APPROVED: 1, MessageStatus.EXPORTED: 2}

#: Columns that can never change after the first save (the text and who/what it is for).
MESSAGE_IMMUTABLE_FIELDS = (
    "company_id",
    "contact_id",
    "angle_id",
    "channel",
    "language",
    "subject",
    "body",
    "is_followup",
    "supersedes_id",
    "model_name",
    "prompt_version",
    "schema_version",
    "created_by_id",
)


class MessageQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["Message"], TenantQuerySet["Message"]
):
    def latest_first(self) -> MessageQuerySet:
        return self.order_by("-created_at", "-id")

    def for_company(self, company: Company | Any) -> MessageQuerySet:
        return self.filter(company=company)

    def current(self) -> MessageQuerySet:
        """Messages nothing supersedes (an edited message drops out in favour of its edit)."""
        return self.filter(superseded_by__isnull=True)

    def latest_for(
        self,
        angle: OutreachAngle | Any,
        contact: Contact | Any,
        channel: str,
        is_followup: bool = False,
    ) -> Message | None:
        """The current message for one (angle, contact, channel, is_followup), or ``None``."""
        return (
            self.current()
            .filter(angle=angle, contact=contact, channel=channel, is_followup=is_followup)
            .latest_first()
            .first()
        )


class Message(TenantModel, UUIDModel):
    """A drafted piece of outreach (Brief section 12).

    Text, recipient, channel and language are immutable: ``save()`` raises
    ``ImmutableRecordError`` if any of them changes, if the status moves backwards or skips a
    step, or if approval data is rewritten. The row cannot be deleted. Change the wording by
    adding a new message with ``supersedes`` (``services.supersede_message``).
    """

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="messages")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="messages")
    angle = models.ForeignKey(OutreachAngle, on_delete=models.PROTECT, related_name="messages")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    language = models.CharField(max_length=16, help_text="Code from the campaign languages.")
    subject = models.CharField(max_length=300, blank=True, help_text="Email only.")
    body = models.TextField()
    is_followup = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=MessageStatus.choices, default=MessageStatus.DRAFT
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
        help_text="The earlier message this row edits.",
    )
    signals = models.ManyToManyField(
        Signal, through="MessageSignal", related_name="messages", blank=True
    )
    data_sources = models.ManyToManyField(
        DataSource, through="MessageSource", related_name="messages", blank=True
    )
    model_name = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    schema_version = models.CharField(max_length=50, blank=True)
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MessageQuerySet.as_manager()

    _loaded: dict[str, Any] | None = None

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["company", "status"], name="out_message_company_status_idx"),
            models.Index(fields=["angle"], name="out_message_angle_idx"),
            models.Index(fields=["contact"], name="out_message_contact_idx"),
            models.Index(fields=["client"], name="out_message_client_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("channel", Channel), name="outreach_message_channel_valid"
            ),
            models.CheckConstraint(
                condition=_in("status", MessageStatus), name="outreach_message_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft") | models.Q(approved_by__isnull=False),
                name="outreach_message_approved_needs_approver",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft") | models.Q(approved_at__isnull=False),
                name="outreach_message_approved_needs_time",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="exported") | models.Q(exported_at__isnull=False),
                name="outreach_message_exported_needs_time",
            ),
            models.CheckConstraint(
                condition=models.Q(channel="email") | models.Q(subject=""),
                name="outreach_message_subject_email_only",
            ),
            models.CheckConstraint(
                condition=~models.Q(body=""), name="outreach_message_body_not_empty"
            ),
            models.CheckConstraint(
                condition=~models.Q(language=""), name="outreach_message_language_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}/{self.language} {self.status}"

    @classmethod
    def from_db(cls, db: str | None, field_names: Any, values: Any, **kwargs: Any) -> Message:
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._snapshot()
        return instance

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._snapshot()

    def _snapshot(self) -> None:
        self._loaded = {
            name: self.__dict__.get(name)
            for name in (*MESSAGE_IMMUTABLE_FIELDS, "status", "approved_by_id", "approved_at")
        }

    @property
    def is_current(self) -> bool:
        return not Message.objects.filter(supersedes=self).exists()

    def sync_client(self) -> None:
        super().sync_client()
        if self.angle.company_id != self.company_id:
            raise TenantMismatchError("Message.angle belongs to a different company.")
        if self.contact.company_id != self.company_id:
            raise TenantMismatchError("Message.contact belongs to a different company.")
        if self.supersedes is not None and self.supersedes.company_id != self.company_id:
            raise TenantMismatchError("Message.supersedes belongs to a different company.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding and self._loaded is not None:
            self._guard_changes(self._loaded)
        super().save(*args, **kwargs)
        self._snapshot()

    def _guard_changes(self, old: dict[str, Any]) -> None:
        for name in MESSAGE_IMMUTABLE_FIELDS:
            if self.__dict__.get(name) != old[name]:
                raise ImmutableRecordError(
                    f"Message.{name.removesuffix('_id')} is immutable; "
                    "add a new message with supersedes instead."
                )
        old_rank = _STATUS_RANK[MessageStatus(old["status"])]
        new_rank = _STATUS_RANK[MessageStatus(self.status)]
        if new_rank < old_rank or new_rank - old_rank > 1:
            raise ImmutableRecordError(
                f"Message status moves forward one step at a time ({old['status']} -> "
                f"{self.status} is not allowed)."
            )
        if old["approved_by_id"] is not None and (
            self.approved_by_id != old["approved_by_id"] or self.approved_at != old["approved_at"]
        ):
            raise ImmutableRecordError("Message approval cannot be changed.")

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ImmutableRecordError("Message is never deleted; supersede it instead.")


class MessageSignal(_EvidenceLink):
    tenant_parent = "message"

    message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="signal_links")
    signal = models.ForeignKey(Signal, on_delete=models.PROTECT, related_name="message_links")

    class Meta:
        db_table = "outreach_message_signals"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["message", "signal"], name="outreach_messagesignal_unique"
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["signal"], name="out_msgsig_signal_idx")
        ]

    def sync_client(self) -> None:
        super().sync_client()
        self._check_target(self.signal, None, self.message)


class MessageSource(_EvidenceLink):
    tenant_parent = "message"

    message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="source_links")
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="message_links"
    )

    class Meta:
        db_table = "outreach_message_sources"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["message", "data_source"], name="outreach_messagesource_unique"
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["data_source"], name="out_msgsrc_source_idx")
        ]

    def sync_client(self) -> None:
        super().sync_client()
        self._check_target(None, self.data_source, self.message)


# ------------------------------------------------------------------ Activity


class ActivityType(models.TextChoices):
    RESEARCHED = "researched", "Researched"
    RECOMMENDED = "recommended", "Recommended"
    DECIDED = "decided", "Decided"
    CONTACTED = "contacted", "Contacted"
    REPLIED = "replied", "Replied"
    MEETING_BOOKED = "meeting_booked", "Meeting booked"
    EXPORTED = "exported", "Exported"
    FEEDBACK = "feedback", "Feedback"


class ActivityQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["Activity"], TenantQuerySet["Activity"]
):
    def latest_first(self) -> ActivityQuerySet:
        return self.order_by("-created_at", "-id")

    def timeline(self, company: Company | Any, types: list[str] | None = None) -> ActivityQuerySet:
        """One company's timeline, newest first, optionally limited to some types."""
        qs = self.filter(company=company)
        if types:
            qs = qs.filter(type__in=types)
        return qs.latest_first()

    def for_campaign(self, campaign: Any, type: str | None = None) -> ActivityQuerySet:
        qs = self.filter(campaign=campaign)
        if type:
            qs = qs.filter(type=type)
        return qs.latest_first()

    def feedback(self) -> ActivityQuerySet:
        return self.filter(type=ActivityType.FEEDBACK)


class Activity(AppendOnlyModel, TenantModel, UUIDModel):
    """One timeline entry (Brief section 14). Append-only. ``campaign`` is copied from the
    company, ``client`` too. Create with ``services.record_activity``."""

    tenant_parent = "company"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="activities")
    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, related_name="activities", editable=False
    )
    type = models.CharField(max_length=24, choices=ActivityType.choices)
    actor = _user_fk()
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, help_text="When it happened.")

    objects = ActivityQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")
        verbose_name_plural = "activities"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["company", "-created_at"], name="out_activity_company_idx"),
            models.Index(
                fields=["campaign", "type", "-created_at"], name="out_activity_campaign_idx"
            ),
            models.Index(fields=["client"], name="out_activity_client_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=_in("type", ActivityType), name="outreach_activity_type_valid"
            )
        ]

    def __str__(self) -> str:
        return f"{self.type} @ {self.created_at:%Y-%m-%d}"

    def sync_client(self) -> None:
        super().sync_client()
        campaign_id = self.company.campaign_id
        if not self.campaign_id:
            self.campaign_id = campaign_id
        elif self.campaign_id != campaign_id:
            raise TenantMismatchError("Activity.campaign does not match its company's campaign.")
