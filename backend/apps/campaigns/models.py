"""Tenancy models: Client, Campaign and the versioned CampaignProfile (issue #39).

Design: ``docs/data-model.md`` and ADR 0009. A Client is the tenant. A Campaign belongs to one
client and points at exactly one current ``CampaignProfile`` (its ICP rules). Profiles are
immutable: editing the rules inserts the next version through
``apps.campaigns.services.create_profile_version`` and moves the pointer, so an old assessment
can still say which rules it used.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from apps.core.base import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    ArchivableModel,
    ArchivableQuerySet,
    BaseModel,
    TenantModel,
    TenantQuerySet,
    UUIDModel,
)
from apps.core.fields import StringListField
from apps.core.roles import Role


class ClientStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class CampaignStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class BusinessModel(models.TextChoices):
    B2B = "b2b", "B2B"
    B2C = "b2c", "B2C"
    BOTH = "both", "B2B and B2C"


def _in(field: str, choices: type[models.TextChoices]) -> models.Q:
    return models.Q(**{f"{field}__in": list(choices.values)})


# ------------------------------------------------------------------ Client


class ClientQuerySet(ArchivableQuerySet["Client"], TenantQuerySet["Client"]):  # type: ignore[override]
    # A client is the tenant itself, so it is scoped by its own id.
    tenant_lookup: ClassVar[str] = "id"


class Client(ArchivableModel, BaseModel):
    """The tenant. Everything else hangs off a client."""

    name = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ClientStatus.choices, default=ClientStatus.ACTIVE
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    archived_status = ClientStatus.ARCHIVED
    restored_status = ClientStatus.ACTIVE

    objects = ClientQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("name",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(archived_at__isnull=True),
                name="campaigns_client_name_unique_active",
            ),
            models.CheckConstraint(
                condition=_in("status", ClientStatus), name="campaigns_client_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ClientStatus.ARCHIVED, archived_at__isnull=False)
                    | (~models.Q(status=ClientStatus.ARCHIVED) & models.Q(archived_at__isnull=True))
                ),
                name="campaigns_client_archived_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return self.name


# ------------------------------------------------------------------ Campaign


class CampaignQuerySet(ArchivableQuerySet["Campaign"], TenantQuerySet["Campaign"]):  # type: ignore[override]
    pass


class Campaign(ArchivableModel, TenantModel, BaseModel):
    """One client, one ICP rule set. ``current_profile`` is always one of its own versions.

    Create campaigns with ``services.create_campaign`` (it makes version 1 and sets the
    pointer in one transaction); change rules only with ``services.create_profile_version``.
    ``current_profile`` is NOT NULL and its FK is deferred to commit, so the campaign row and
    its version 1 are inserted together. On PostgreSQL a composite foreign key additionally
    guarantees the profile belongs to this campaign (migration ``0002``).
    """

    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=CampaignStatus.choices, default=CampaignStatus.DRAFT
    )
    current_profile = models.ForeignKey(
        "campaigns.CampaignProfile",
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    archived_status = CampaignStatus.ARCHIVED
    restored_status = CampaignStatus.DRAFT

    objects = CampaignQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("name",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                "client",
                Lower("name"),
                condition=models.Q(archived_at__isnull=True),
                name="campaigns_campaign_name_unique_active",
            ),
            models.CheckConstraint(
                condition=_in("status", CampaignStatus), name="campaigns_campaign_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=CampaignStatus.ARCHIVED, archived_at__isnull=False)
                    | (
                        ~models.Q(status=CampaignStatus.ARCHIVED)
                        & models.Q(archived_at__isnull=True)
                    )
                ),
                name="campaigns_campaign_archived_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.current_profile_id and self.current_profile.campaign_id != self.pk:
            raise ValidationError(
                {"current_profile": "The current profile must be a version of this campaign."}
            )

    def activate(self) -> None:
        """draft -> active. A campaign under an archived client cannot go active."""
        if self.is_archived:
            raise ValidationError("An archived campaign cannot be activated; restore it first.")
        if self.client.is_archived:
            raise ValidationError("A campaign under an archived client cannot be activated.")
        self.status = CampaignStatus.ACTIVE
        self.save(update_fields=["status", "updated_at"])

    def restore(self) -> None:
        if self.is_archived and self.client.is_archived:
            raise ValidationError("Restore the client before restoring its campaign.")
        super().restore()


# ------------------------------------------------------------------ CampaignProfile


class CampaignProfileQuerySet(  # type: ignore[override]
    AppendOnlyQuerySet["CampaignProfile"], TenantQuerySet["CampaignProfile"]
):
    pass


class CampaignProfile(AppendOnlyModel, TenantModel, UUIDModel):
    """One immutable version of a campaign's ICP rules (Brief section 3).

    Never edited or deleted: ``save`` on an existing row, ``delete`` and bulk update/delete all
    raise ``ImmutableRecordError``. To change the rules call
    ``services.create_profile_version``. List fields keep the order given; for
    ``preferred_buyer_titles`` the first entry is the most preferred.
    """

    tenant_parent = "campaign"

    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="profiles")
    version = models.PositiveIntegerField()
    offer = models.TextField()
    countries = StringListField(help_text="ISO 3166-1 alpha-2 codes, upper case, e.g. SA.")
    industries = StringListField()
    company_size_min = models.PositiveIntegerField(null=True, blank=True)
    company_size_max = models.PositiveIntegerField(null=True, blank=True)
    business_model = models.CharField(
        max_length=8, choices=BusinessModel.choices, default=BusinessModel.B2B
    )
    excluded_industries = StringListField()
    excluded_company_types = StringListField()
    target_departments = StringListField()
    preferred_buyer_titles = StringListField(help_text="Ordered, first is most preferred.")
    outreach_languages = StringListField(help_text="Language codes, lower case, e.g. en, ar.")
    custom_rules = models.TextField(blank=True, help_text="Free-form qualification notes.")
    change_note = models.TextField(blank=True, help_text="Why this version was made.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CampaignProfileQuerySet.as_manager()

    #: Fields that make up the rules (everything a new version may change).
    RULE_FIELDS: ClassVar[tuple[str, ...]] = (
        "offer",
        "countries",
        "industries",
        "company_size_min",
        "company_size_max",
        "business_model",
        "excluded_industries",
        "excluded_company_types",
        "target_departments",
        "preferred_buyer_titles",
        "outreach_languages",
        "custom_rules",
    )

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("campaign_id", "-version")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["campaign", "version"], name="campaigns_profile_version_unique"
            ),
            # Target of the composite FK that proves Campaign.current_profile is its own version.
            models.UniqueConstraint(
                fields=["campaign", "id"], name="campaigns_profile_campaign_id_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="campaigns_profile_version_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(company_size_min__isnull=True)
                    | models.Q(company_size_max__isnull=True)
                    | models.Q(company_size_min__lte=models.F("company_size_max"))
                ),
                name="campaigns_profile_size_min_lte_max",
            ),
            models.CheckConstraint(
                condition=_in("business_model", BusinessModel),
                name="campaigns_profile_business_model_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(offer=""), name="campaigns_profile_offer_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id} v{self.version}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        low, high = self.company_size_min, self.company_size_max
        if low is not None and high is not None and low > high:
            errors["company_size_max"] = "Maximum company size must be at least the minimum."
        if not (self.offer or "").strip():
            errors["offer"] = "Describe the offer."
        bad = [c for c in self.countries if not _is_country_code(c)]
        if bad:
            errors["countries"] = f"Use ISO 3166-1 alpha-2 codes in upper case, not {bad}."
        if errors:
            raise ValidationError(errors)


def _is_country_code(value: str) -> bool:
    return len(value) == 2 and value.isascii() and value.isalpha() and value.isupper()


# ------------------------------------------------------------------ ClientMembership (issue #46)


class ClientMembershipQuerySet(  # type: ignore[override]
    ArchivableQuerySet["ClientMembership"], TenantQuerySet["ClientMembership"]
):
    pass


class ClientMembership(ArchivableModel, TenantModel, BaseModel):
    """Which user may work on which client, and as what (``apps.core.roles.Role``).

    One row per (user, client), ever: revoking archives the row and granting again restores it
    with the new role, so the history of "was a member" is kept. Only non-archived rows grant
    access (``apps.core.tenancy``). Change memberships through ``apps.campaigns.memberships``,
    not by hand. The global admin flag is ``User.is_superuser``, not a membership.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="client_memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices)

    objects = ClientMembershipQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("client_id", "user_id")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "client"], name="campaigns_membership_user_client_unique"
            ),
            models.CheckConstraint(
                condition=_in("role", Role), name="campaigns_membership_role_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} is {self.role} in {self.client_id}"
