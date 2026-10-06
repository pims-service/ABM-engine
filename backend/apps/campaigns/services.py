"""Write path for clients, campaigns and their ICP rules.

These functions are the only supported way to create, change, archive or restore a client or
campaign, and each one writes an audit entry (``apps.core.audit``, issue #44) in the same
transaction, naming the acting user. Audit is an explicit service call, not a signal: signals
cannot know the actor, miss bulk ``update()``s and fire for fixtures and migrations too.
Profiles are immutable, so "editing" rules always means a new version.
Permission checks belong to the API layer (issue #46); these functions only enforce data rules.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import record_change, snapshot
from apps.core.models import AuditAction, Job

from .models import Campaign, CampaignProfile, Client

CLIENT_AUDIT_FIELDS = ("name", "notes", "status", "archived_at")
CAMPAIGN_AUDIT_FIELDS = ("name", "status", "archived_at", "current_profile")
CLIENT_EDITABLE_FIELDS = ("name", "notes")
CAMPAIGN_EDITABLE_FIELDS = ("name",)

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
    record_change(
        AuditAction.CREATE,
        "campaign",
        campaign.pk,
        actor=user,
        client=client,
        after={**snapshot(campaign, CAMPAIGN_AUDIT_FIELDS), "profile_version": 1},
    )
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
    previous_version = current.version
    changed_fields = [name for name, value in _rules(current).items() if values[name] != value]
    profile.save()

    locked.current_profile = profile
    locked.save(update_fields=["current_profile", "updated_at"])
    # A reference to the new version (the old and new rules are both kept as rows), not a copy.
    record_change(
        AuditAction.CREATE,
        "campaign_profile",
        profile.pk,
        actor=user,
        client=locked.client_id,
        after={
            "campaign_id": str(locked.pk),
            "version": profile.version,
            "previous_version": previous_version,
            "changed_fields": changed_fields,
            "change_note": profile.change_note,
        },
    )
    campaign.current_profile = profile
    campaign.updated_at = locked.updated_at or timezone.now()
    return profile


# ------------------------------------------------------------------ clients


def audit_client_created(client: Client, user: User | None = None) -> None:
    """Audit entry for a client saved elsewhere (the admin add form); ``create_client`` calls it."""
    record_change(
        AuditAction.CREATE,
        "client",
        client.pk,
        actor=user,
        client=client,
        after=snapshot(client, CLIENT_AUDIT_FIELDS),
    )


def _name_taken(model: Any, name: str, exclude_pk: Any = None, **scope: Any) -> bool:
    qs = model.objects.active().filter(**scope).annotate(lowered=Lower("name"))
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return bool(qs.filter(lowered=name.lower()).exists())


@transaction.atomic
def create_client(name: str, notes: str = "", user: User | None = None) -> Client:
    client = Client(name=name.strip(), notes=notes, created_by=user)
    client.full_clean(validate_constraints=False)
    if _name_taken(Client, client.name):
        raise ValidationError({"name": "An active client with that name already exists."})
    client.save()
    audit_client_created(client, user)
    return client


@transaction.atomic
def update_client(client: Client, user: User | None = None, **changes: Any) -> Client:
    """Change ``name`` and/or ``notes`` and audit the diff. No entry if nothing changed."""
    unknown = sorted(set(changes) - set(CLIENT_EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown field."))
    locked = Client.objects.select_for_update().get(pk=client.pk)
    before = snapshot(locked, CLIENT_AUDIT_FIELDS)
    for key, value in changes.items():
        setattr(locked, key, value.strip() if key == "name" else value)
    locked.full_clean(validate_constraints=False)
    if _name_taken(Client, locked.name, exclude_pk=locked.pk):
        raise ValidationError({"name": "An active client with that name already exists."})
    locked.save(update_fields=[*changes, "updated_at"])
    record_change(
        AuditAction.UPDATE,
        "client",
        locked.pk,
        actor=user,
        client=locked,
        before=before,
        after=snapshot(locked, CLIENT_AUDIT_FIELDS),
    )
    client.refresh_from_db()
    return client


def _change_client(
    client: Client, action: str, user: User | None, mutate: Callable[[Client], None]
) -> Client:
    with transaction.atomic():
        locked = Client.objects.select_for_update().get(pk=client.pk)
        before = snapshot(locked, CLIENT_AUDIT_FIELDS)
        mutate(locked)
        record_change(
            action,
            "client",
            locked.pk,
            actor=user,
            client=locked,
            before=before,
            after=snapshot(locked, CLIENT_AUDIT_FIELDS),
        )
    client.refresh_from_db()
    return client


def archive_client(client: Client, user: User | None = None) -> Client:
    """Archive a client (idempotent). Refused while it has queued or running jobs."""

    def mutate(locked: Client) -> None:
        if not locked.is_archived and Job.objects.for_client(locked).active().exists():
            raise ValidationError("Cannot archive a client with queued or running jobs.")
        locked.archive()

    return _change_client(client, AuditAction.ARCHIVE, user, mutate)


def restore_client(client: Client, user: User | None = None) -> Client:
    return _change_client(client, AuditAction.RESTORE, user, lambda locked: locked.restore())


# ------------------------------------------------------------------ campaigns


def _change_campaign(
    campaign: Campaign, action: str, user: User | None, mutate: Callable[[Campaign], None]
) -> Campaign:
    """Lock the campaign, run ``mutate(locked)``, audit the field diff, sync ``campaign``."""
    with transaction.atomic():
        locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
        before = snapshot(locked, CAMPAIGN_AUDIT_FIELDS)
        mutate(locked)
        record_change(
            action,
            "campaign",
            locked.pk,
            actor=user,
            client=locked.client_id,
            before=before,
            after=snapshot(locked, CAMPAIGN_AUDIT_FIELDS),
        )
    campaign.refresh_from_db()
    return campaign


def update_campaign(campaign: Campaign, user: User | None = None, **changes: Any) -> Campaign:
    """Rename a campaign (rules change through ``create_profile_version``) and audit the diff."""
    unknown = sorted(set(changes) - set(CAMPAIGN_EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown field."))

    def mutate(locked: Campaign) -> None:
        if locked.is_archived:
            raise ValidationError("Archived campaigns are read-only.")
        if "name" in changes:
            locked.name = str(changes["name"]).strip()
        locked.clean_fields(exclude=["current_profile"])
        if _name_taken(Campaign, locked.name, exclude_pk=locked.pk, client_id=locked.client_id):
            raise ValidationError(
                {"name": "This client already has an active campaign with that name."}
            )
        locked.save(update_fields=["name", "updated_at"])

    return _change_campaign(campaign, AuditAction.UPDATE, user, mutate)


def activate_campaign(campaign: Campaign, user: User | None = None) -> Campaign:
    """draft -> active, audited."""
    return _change_campaign(campaign, AuditAction.UPDATE, user, lambda locked: locked.activate())


def archive_campaign(campaign: Campaign, user: User | None = None) -> Campaign:
    """Archive a campaign (idempotent). Refused while it has queued or running jobs."""

    def mutate(locked: Campaign) -> None:
        if not locked.is_archived and Job.objects.filter(campaign=locked).active().exists():
            raise ValidationError("Cannot archive a campaign with queued or running jobs.")
        locked.archive()

    return _change_campaign(campaign, AuditAction.ARCHIVE, user, mutate)


def restore_campaign(campaign: Campaign, user: User | None = None) -> Campaign:
    return _change_campaign(campaign, AuditAction.RESTORE, user, lambda locked: locked.restore())
