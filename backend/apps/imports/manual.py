"""Manual company entry (issue #59): one company typed in by a person, through the shared
import pipeline so history and counters look like every other channel.

``add_company_manually`` opens a one-row ``ImportBatch`` (source ``manual``), runs
``imports.services.process_row`` (validate, dedupe, create or link, record the outcome), finishes
the batch and audits the change. Permission checks belong to the API layer.

Input rules (the manual entry rule of Brief section 4A, ``require_identifier=True``):

* a ``name`` is always required (a company row without a name has nothing to review), and
* at least one identifier: a ``website`` or a ``profile_url``;
* ``country`` is optional (ISO 3166-1 alpha-2);
* ``input`` is an optional single free-text box. ``classify_input`` decides what it holds: a
  URL or bare domain fills ``website``, a recognised profile URL fills ``profile_url``, anything
  else fills ``name``. It only fills a field that was left empty: naming the same field twice is
  an ``input_conflicts_with_field`` error, and a ``javascript:``-style value is ``input_invalid``.

Invalid input is reported as field errors with the stable codes of ``apps.imports.schema``
(plus the two ``input`` codes below) and creates nothing, not even a batch. Once the input is
valid the batch exists whatever happens next (a duplicate, or a company that the pipeline
refuses, is a recorded outcome).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.companies.normalize import classify_input
from apps.core.audit import record, record_change, snapshot
from apps.core.models import AuditAction

from .models import ImportBatch, ImportRow, ImportRowOutcome, ImportSource
from .schema import (
    ROW_FIELD,
    CompanyInput,
    FieldError,
    InputErrors,
    validate_company_input,
)
from .services import create_batch, finalize_batch, process_row

__all__ = [
    "COMPANY_AUDIT_FIELDS",
    "INPUT_CONFLICT",
    "INPUT_INVALID",
    "ManualEntry",
    "add_company_manually",
    "validate_manual_entry",
]

INPUT_INVALID = "input_invalid"
INPUT_CONFLICT = "input_conflicts_with_field"

_INPUT_MESSAGES = {
    INPUT_INVALID: "Enter a company name, a website or a profile URL.",
    INPUT_CONFLICT: "This value is a {slot}, but `{slot}` was also given; send only one of them.",
}
_SLOT_OF_KIND = {
    "url": "website",
    "domain": "website",
    "profile_url": "profile_url",
    "name": "name",
}

COMPANY_AUDIT_FIELDS = (
    "name",
    "website",
    "domain",
    "profile_url",
    "country",
    "input_source",
    "status",
    "archived_at",
    "possible_duplicate_of",
)


@dataclass(frozen=True)
class ManualEntry:
    """What ``add_company_manually`` did: the batch, its single row and the row's company.

    ``row.outcome`` is ``created``, ``duplicate``, ``restored`` or ``failed`` (then ``company``
    is None and ``row.error_code`` says why).
    """

    batch: ImportBatch
    row: ImportRow

    @property
    def company(self) -> Company | None:
        return self.row.company

    @property
    def failed(self) -> bool:
        return self.row.outcome == ImportRowOutcome.FAILED

    @property
    def possible_duplicate(self) -> bool:
        return self.row.outcome == ImportRowOutcome.CREATED and self.row.match_strength == "weak"


def validate_manual_entry(data: Mapping[str, Any]) -> tuple[dict[str, Any], InputErrors | None]:
    """Resolve ``input`` into the four fields and validate with ``require_identifier=True``.

    Returns ``(raw, errors)``: ``raw`` is the mapping to hand to the pipeline (the four fields,
    plus ``input`` when it was used, kept for the history); ``errors`` is None when it is valid.
    """
    raw: dict[str, Any] = {
        key: data.get(key) for key in ("name", "website", "profile_url", "country") if key in data
    }
    errors: list[FieldError] = []
    free_text = data.get("input")
    if isinstance(free_text, str) and free_text.strip():
        raw["input"] = free_text
        classified = classify_input(free_text)
        slot = _SLOT_OF_KIND.get(classified.kind)
        if slot is None or classified.value is None:
            errors.append(FieldError("input", INPUT_INVALID, _INPUT_MESSAGES[INPUT_INVALID]))
        elif isinstance(raw.get(slot), str) and raw[slot].strip():
            message = _INPUT_MESSAGES[INPUT_CONFLICT].format(slot=slot)
            errors.append(FieldError("input", INPUT_CONFLICT, message))
        else:
            raw[slot] = classified.value
    checked = validate_company_input(raw, require_identifier=True)
    if isinstance(checked, InputErrors):
        errors.extend(checked.errors)
    if errors:
        return raw, InputErrors(tuple(errors))
    assert isinstance(checked, CompanyInput)  # noqa: S101 - no errors means a valid input
    return raw, None


def add_company_manually(
    campaign: Campaign, raw: Mapping[str, Any], *, user: User | None = None
) -> ManualEntry:
    """Add one company through a one-row manual batch. ``raw`` must already be valid
    (``validate_manual_entry``). Raises ``ValidationError`` for an archived campaign or client."""
    if campaign.client.is_archived:
        raise ValidationError("Cannot add a company to an archived client.")
    with transaction.atomic():
        batch = create_batch(campaign, ImportSource.MANUAL, user=user, total_count=1)
        row = process_row(batch, 1, raw, user=user)
        finalize_batch(batch)
        company = row.company
        if company is not None and row.outcome == ImportRowOutcome.CREATED:
            record_change(
                AuditAction.CREATE,
                "company",
                company.pk,
                actor=user,
                client=campaign.client_id,
                after={**snapshot(company, COMPANY_AUDIT_FIELDS), "import_batch": str(batch.pk)},
            )
        elif company is not None and row.outcome == ImportRowOutcome.RESTORED:
            record(
                action=AuditAction.RESTORE,
                object_type="company",
                object_id=company.pk,
                actor=user,
                client=campaign.client_id,
                before={"archived": True},
                after={"archived": False, "import_batch": str(batch.pk)},
            )
    batch.refresh_from_db()
    return ManualEntry(batch=batch, row=row)


def field_errors(errors: InputErrors) -> dict[str, list[dict[str, str]]]:
    """``InputErrors`` as API details: ``{field: [{code, message}]}``; row-level errors under
    ``non_field_errors``."""
    details = errors.as_dict()
    if ROW_FIELD in details:
        details["non_field_errors"] = details.pop(ROW_FIELD)
    return details
