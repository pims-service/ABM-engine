"""Write and read path for signals (issue #41).

Permission checks belong to the API layer (issue #46); these functions enforce data rules.

* ``create_signal``: append a signal. A source and an event date are required.
* ``supersede_signal``: correct a signal with a new row that points at the old one.
* ``retire_signal``: a wrong signal with no replacement is superseded by an immediately
  expired ``other`` row (data-model.md, "History").
* ``company_trigger_state``: the computed Trigger Yes/No plus the fresh signals. Nothing is
  stored, and zero signals is a normal "No".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company, DataSource

from .models import Signal, SignalType

__all__ = [
    "TriggerState",
    "company_trigger_state",
    "create_signal",
    "retire_signal",
    "supersede_signal",
]


@dataclass
class TriggerState:
    """Computed trigger answer for one company at one instant."""

    as_of: datetime
    signals: list[Signal] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.signals)

    @property
    def label(self) -> str:
        return "Yes" if self.triggered else "No"


def company_trigger_state(company: Company, as_of: datetime | None = None) -> TriggerState:
    """Trigger Yes when the company has at least one fresh, unsuperseded signal at ``as_of``."""
    when = as_of or timezone.now()
    signals = list(Signal.objects.for_company(company).fresh(when).latest_first())
    return TriggerState(as_of=when, signals=signals)


@transaction.atomic
def create_signal(
    company: Company,
    data_source: DataSource,
    type: str,
    evidence: str,
    event_date: date | None,
    *,
    detected_at: datetime | None = None,
    expires_at: datetime | None = None,
    supersedes: Signal | None = None,
    model_name: str = "",
    prompt_version: str = "",
    schema_version: str = "",
    user: User | None = None,
) -> Signal:
    """Append a signal. No source or no event date is a ``ValidationError``."""
    if data_source is None:
        raise ValidationError({"data_source": "A signal needs a data source."})
    if event_date is None:
        raise ValidationError({"event_date": "A signal needs the date of the event."})
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")
    if data_source.client_id != company.client_id:
        raise ValidationError({"data_source": "The data source belongs to a different client."})
    if supersedes is not None:
        if supersedes.company_id != company.pk:
            raise ValidationError({"supersedes": "The signal belongs to a different company."})
        if not supersedes.is_current:
            raise ValidationError({"supersedes": "That signal is already superseded."})

    extra = {} if detected_at is None else {"detected_at": detected_at}
    signal = Signal(
        company=company,
        data_source=data_source,
        type=type,
        evidence=evidence.strip(),
        event_date=event_date,
        expires_at=expires_at,
        supersedes=supersedes,
        model_name=model_name,
        prompt_version=prompt_version,
        schema_version=schema_version,
        created_by=user,
        **extra,
    )
    signal.full_clean(
        exclude=["client", "company", "data_source", "supersedes"],
        validate_unique=False,
        validate_constraints=False,
    )
    try:
        with transaction.atomic():
            signal.save()
    except IntegrityError as exc:  # lost a race to another correction of the same signal
        if supersedes is not None and not supersedes.is_current:
            raise ValidationError({"supersedes": "That signal is already superseded."}) from exc
        raise
    return signal


def supersede_signal(
    old: Signal,
    data_source: DataSource,
    evidence: str,
    event_date: date,
    *,
    type: str | None = None,
    **kwargs: object,
) -> Signal:
    """Correct ``old`` with a new row (type defaults to the old one). ``old`` is untouched."""
    return create_signal(
        old.company,
        data_source,
        type or old.type,
        evidence,
        event_date,
        supersedes=old,
        **kwargs,  # type: ignore[arg-type]
    )


def retire_signal(
    old: Signal,
    data_source: DataSource,
    reason: str,
    *,
    event_date: date | None = None,
    user: User | None = None,
) -> Signal:
    """Withdraw a wrong signal that nothing replaces: an ``other`` row that expires at once."""
    now = timezone.now()
    return create_signal(
        old.company,
        data_source,
        SignalType.OTHER,
        reason,
        event_date or now.date(),
        detected_at=now,
        expires_at=now,
        supersedes=old,
        user=user,
    )
