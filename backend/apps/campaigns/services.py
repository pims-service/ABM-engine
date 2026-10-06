"""Write path for campaigns and their ICP rules.

``create_campaign`` and ``create_profile_version`` are the only supported ways to create a
campaign or change its rules. Profiles are immutable, so "editing" always means a new version.
Permission checks belong to the API layer (issue #46); these functions only enforce data rules.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import User

from .models import Campaign, CampaignProfile, Client

LIST_FIELDS = (
    "countries",
    "industries",
    "excluded_industries",
    "excluded_company_types",
    "target_departments",
    "preferred_buyer_titles",
    "outreach_languages",
)
TEXT_FIELDS = ("offer", "custom_rules")
EDITABLE_FIELDS = (*CampaignProfile.RULE_FIELDS, "change_note")


class ProfileUnchangedError(ValidationError):
    """The submitted rules equal the current version, so no new version was made."""


def _clean_list(field: str, value: Any) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValidationError({field: "Expected a list of strings."})
    items: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValidationError({field: "Expected a list of strings."})
        item = raw.strip()
        if field == "countries":
            item = item.upper()
        elif field == "outreach_languages":
            item = item.lower()
        key = item.casefold()
        if item and key not in seen:  # order is kept: first occurrence wins
            seen.add(key)
            items.append(item)
    return items


def normalize_profile_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Trim text, drop blank/duplicate list entries (keeping order), fix case of codes."""
    unknown = sorted(set(data) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown field."))
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in LIST_FIELDS:
            out[key] = _clean_list(key, value)
        elif key in (*TEXT_FIELDS, "change_note"):
            out[key] = (value or "").strip()
        else:
            out[key] = value
    return out


def _rules(profile: CampaignProfile) -> dict[str, Any]:
    return {name: getattr(profile, name) for name in CampaignProfile.RULE_FIELDS}


def _build_profile(
    campaign: Campaign,
    version: int,
    values: dict[str, Any],
    user: User | None,
    pk: uuid.UUID | None = None,
) -> CampaignProfile:
    change_note = values.pop("change_note", "")
    profile = CampaignProfile(
        campaign=campaign, version=version, created_by=user, change_note=change_note, **values
    )
    if pk is not None:
        profile.id = pk
    profile.full_clean(
        exclude=["client", "campaign"], validate_unique=False, validate_constraints=False
    )
    return profile


@transaction.atomic
def create_campaign(
    client: Client,
    name: str,
    data: Mapping[str, Any],
    user: User | None = None,
) -> Campaign:
    """Create a draft campaign and its version 1 profile in one transaction."""
    name = name.strip()
    if not name:
        raise ValidationError({"name": "A campaign needs a name."})
    if client.is_archived:
        raise ValidationError("Cannot add a campaign to an archived client.")
    if (
        Campaign.objects.filter(client=client, archived_at__isnull=True)
        .annotate(lowered=Lower("name"))
        .filter(lowered=name.lower())
        .exists()
    ):
        raise ValidationError(
            {"name": "This client already has an active campaign with that name."}
        )

    profile_id = uuid.uuid4()
    campaign = Campaign(client=client, name=name, created_by=user, current_profile_id=profile_id)
    campaign.clean_fields(exclude=["current_profile"])
    profile = _build_profile(campaign, 1, normalize_profile_data(data), user, pk=profile_id)
    campaign.save()  # the FK to the profile is deferred until commit
    profile.save()
    return campaign


@transaction.atomic
def create_profile_version(
    campaign: Campaign,
    data: Mapping[str, Any],
    user: User | None = None,
) -> CampaignProfile:
    """Make the next profile version and point the campaign at it. The only way to edit rules.

    ``data`` may hold any subset of the rule fields plus ``change_note``; fields left out are
    carried over from the current version. The campaign row is locked first, so concurrent
    edits get consecutive version numbers (the unique ``(campaign, version)`` constraint is the
    backstop). Old versions are never touched. Raises ``ProfileUnchangedError`` when nothing
    would change and ``ValidationError`` for invalid data or an archived campaign.
    """
    # Lock the campaign row alone: joining current_profile here would make PostgreSQL re-check
    # the join against a stale profile row after waiting for a concurrent edit, and the row
    # would vanish. The current profile is read after the lock is held.
    locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
    if locked.is_archived:
        raise ValidationError("Archived campaigns are read-only.")

    changes = normalize_profile_data(data)
    current = locked.current_profile
    values = {**_rules(current), **changes}
    if _rules(current) == {k: v for k, v in values.items() if k != "change_note"}:
        raise ProfileUnchangedError("These rules are identical to the current version.")

    latest = CampaignProfile.objects.filter(campaign=locked).aggregate(top=Max("version"))["top"]
    profile = _build_profile(locked, (latest or 0) + 1, values, user)
    profile.save()

    locked.current_profile = profile
    locked.save(update_fields=["current_profile", "updated_at"])
    campaign.current_profile = profile
    campaign.updated_at = locked.updated_at or timezone.now()
    return profile
