"""Serializers for the campaign endpoints (issue #48).

Input is validated here (field-level errors in the standard envelope); the rules themselves are
written by ``services.create_profile_version`` / ``create_campaign``, never by a serializer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from apps.campaigns.models import BusinessModel, Campaign, CampaignProfile, CampaignStatus
from apps.campaigns.reference import ISO_3166_ALPHA2, supported_languages
from apps.campaigns.rules_summary import RULES_SUMMARY_SCHEMA_VERSION

MAX_INT = 2_147_483_647
MAX_LIST_ITEMS = 100

#: Keys that are planned but not stored yet: answered with a clear 400, never silently dropped.
RESERVED_FIELDS = ("structured_rules", "rules")
NOT_SUPPORTED = "Structured rules are not supported yet; use custom_rules for free-form notes."


class StrictCharField(serializers.CharField):
    """A string only: numbers, booleans and nulls are rejected instead of coerced."""

    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StringListInput(serializers.ListField):
    """A JSON list of non-blank strings (at most ``MAX_LIST_ITEMS``). Order is kept."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("child", StrictCharField(max_length=200))
        kwargs.setdefault("max_length", MAX_LIST_ITEMS)
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)


class CampaignProfileSerializer(serializers.ModelSerializer[CampaignProfile]):
    """One immutable profile version (the rules of Brief section 3)."""

    countries = serializers.ListField(child=serializers.CharField(), read_only=True)
    industries = serializers.ListField(child=serializers.CharField(), read_only=True)
    excluded_industries = serializers.ListField(child=serializers.CharField(), read_only=True)
    excluded_company_types = serializers.ListField(child=serializers.CharField(), read_only=True)
    target_departments = serializers.ListField(child=serializers.CharField(), read_only=True)
    preferred_buyer_titles = serializers.ListField(
        child=serializers.CharField(), read_only=True, help_text="Ordered, most preferred first."
    )
    outreach_languages = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = CampaignProfile
        fields = (
            "id",
            "campaign",
            "version",
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
            "change_note",
            "created_by",
            "created_at",
        )
        read_only_fields = fields


class CampaignSerializer(serializers.ModelSerializer[Campaign]):
    """A campaign with its current profile. ``status`` changes only through the actions."""

    profile_version = serializers.IntegerField(source="current_profile.version", read_only=True)
    profile = CampaignProfileSerializer(source="current_profile", read_only=True)

    class Meta:
        model = Campaign
        fields = (
            "id",
            "client",
            "name",
            "status",
            "archived_at",
            "profile_version",
            "profile",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class RejectUnknownFields:
    """Mixin for serializers: unknown keys are field errors (reported together with the rest).

    Nothing is ever silently dropped. ``structured_rules`` gets a specific message.
    """

    unknown_field_message = "Unknown field."

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        errors: dict[str, Any] = {}
        if isinstance(data, Mapping):
            for key in data:
                if key not in self.fields:  # type: ignore[attr-defined]
                    errors[str(key)] = [
                        NOT_SUPPORTED if key in RESERVED_FIELDS else self.unknown_field_message
                    ]
        try:
            value = super().to_internal_value(data)  # type: ignore[misc]
        except serializers.ValidationError as exc:
            if not isinstance(exc.detail, dict):
                raise
            errors = {**exc.detail, **errors}
        if errors:
            raise serializers.ValidationError(errors)
        return dict(value)


class CampaignProfileInputSerializer(RejectUnknownFields, serializers.Serializer[dict[str, Any]]):
    """The rule fields of a campaign. Anything not listed is rejected, nothing is dropped.

    On create ``offer`` is required and the rest defaults to empty. On PUT/PATCH a field left out
    keeps its value from the current version; send ``[]`` / ``null`` / ``""`` to clear it.
    """

    offer = StrictCharField(max_length=5000, help_text="What the client sells, in plain words.")
    countries = StringListInput(
        help_text="ISO 3166-1 alpha-2 codes (case-insensitive input, stored upper case)."
    )
    industries = StringListInput()
    company_size_min = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=MAX_INT
    )
    company_size_max = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=MAX_INT,
        help_text="Must be at least `company_size_min` when both are set.",
    )
    business_model = serializers.ChoiceField(choices=BusinessModel.choices, required=False)
    excluded_industries = StringListInput()
    excluded_company_types = StringListInput()
    target_departments = StringListInput()
    preferred_buyer_titles = StringListInput(
        help_text="Ordered, most preferred first. Duplicates are removed keeping the first."
    )
    outreach_languages = StringListInput(
        help_text="Supported language codes (`settings.OUTREACH_LANGUAGES`, `en` and `ar`)."
    )
    custom_rules = StrictCharField(
        max_length=10000, required=False, allow_blank=True, help_text="Free-form notes."
    )
    change_note = StrictCharField(
        max_length=1000, required=False, allow_blank=True, help_text="Why this version is made."
    )

    def validate_countries(self, value: list[str]) -> list[str]:
        codes = [item.strip().upper() for item in value]
        bad = [c for c in codes if c not in ISO_3166_ALPHA2]
        if bad:
            raise serializers.ValidationError(
                f"Unknown country code(s): {', '.join(dict.fromkeys(bad))}. "
                "Use ISO 3166-1 alpha-2 codes such as SA or AE."
            )
        return codes

    def validate_outreach_languages(self, value: list[str]) -> list[str]:
        codes = [item.strip().lower() for item in value]
        allowed = supported_languages()
        bad = [c for c in codes if c not in allowed]
        if bad:
            raise serializers.ValidationError(
                f"Unsupported language(s): {', '.join(dict.fromkeys(bad))}. "
                f"Supported: {', '.join(allowed)}."
            )
        return codes

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        current: CampaignProfile | None = self.context.get("current_profile")
        low = attrs.get("company_size_min", current.company_size_min if current else None)
        high = attrs.get("company_size_max", current.company_size_max if current else None)
        if low is not None and high is not None and low > high:
            raise serializers.ValidationError(
                {"company_size_max": ["Maximum company size must be at least the minimum."]}
            )
        return attrs


class CampaignWriteSerializer(RejectUnknownFields, serializers.Serializer[dict[str, Any]]):
    """Body of create (POST) and edit (PUT/PATCH).

    ``name`` goes through ``update_campaign``; ``profile`` (the rules) through
    ``create_profile_version``, which makes a new version only when something changed.
    """

    client = serializers.UUIDField(
        required=False,
        help_text="Required on `POST /campaigns/`; taken from the URL on the nested route; "
        "cannot be changed on edit.",
    )
    name = StrictCharField(max_length=200)
    profile = CampaignProfileInputSerializer(required=False)

    unknown_field_message = "Unknown field. Rules go inside `profile`."

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        instance: Campaign | None = self.instance  # type: ignore[assignment]
        errors: dict[str, list[str]] = {}
        if instance is None:
            if "client" not in attrs:
                errors["client"] = ["This field is required."]
            if "profile" not in attrs:
                errors["profile"] = ["This field is required."]
        else:
            if "client" in attrs and attrs["client"] != instance.client_id:
                errors["client"] = ["A campaign cannot be moved to another client."]
            if not self.partial and "profile" not in attrs:
                errors["profile"] = ["This field is required."]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CampaignListQuerySerializer(serializers.Serializer[dict[str, str]]):
    """Query parameters of the campaign list (validated by the view; documentation for OpenAPI)."""

    client = serializers.UUIDField(
        required=False, help_text="Only campaigns of this client (ignored on the nested route)."
    )
    status = serializers.ChoiceField(
        choices=CampaignStatus.choices,
        required=False,
        help_text="Only campaigns with this status. `archived` implies `archived=true` unless "
        "`archived` is given.",
    )
    archived = serializers.ChoiceField(
        choices=["false", "true", "all"],
        required=False,
        help_text="`false` (default) hides archived campaigns, `true` shows only archived "
        "ones, `all` shows both.",
    )


class CampaignCloneSerializer(serializers.Serializer[dict[str, str]]):
    name = StrictCharField(
        max_length=200,
        required=False,
        help_text='Name of the copy. Default: "<name> (copy)", numbered if that is taken.',
    )


class RulesSummaryQuerySerializer(serializers.Serializer[dict[str, int]]):
    version = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=MAX_INT,
        help_text="Profile version to summarise. Default: the current version.",
    )


# ---------------------------------------------------------------- rules summary


class RulesSummaryCampaignSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class RulesSummaryCompanySizeSerializer(serializers.Serializer[dict[str, Any]]):
    min = serializers.IntegerField(allow_null=True)
    max = serializers.IntegerField(allow_null=True)


class RulesSummaryTargetingSerializer(serializers.Serializer[dict[str, Any]]):
    countries = serializers.ListField(child=serializers.CharField())
    industries = serializers.ListField(child=serializers.CharField())
    company_size = RulesSummaryCompanySizeSerializer()
    business_model = serializers.ChoiceField(choices=BusinessModel.choices)


class RulesSummaryExclusionsSerializer(serializers.Serializer[dict[str, Any]]):
    industries = serializers.ListField(child=serializers.CharField())
    company_types = serializers.ListField(child=serializers.CharField())


class RulesSummaryBuyersSerializer(serializers.Serializer[dict[str, Any]]):
    target_departments = serializers.ListField(child=serializers.CharField())
    preferred_buyer_titles = serializers.ListField(
        child=serializers.CharField(), help_text="Most preferred first."
    )


class RulesSummaryOutreachSerializer(serializers.Serializer[dict[str, Any]]):
    languages = serializers.ListField(child=serializers.CharField())


class RulesSummarySerializer(serializers.Serializer[dict[str, Any]]):
    """The profile in the structure the AI prompts consume. Versioned by `schema_version`.

    Built by `apps.campaigns.rules_summary.build_rules_summary`; see that module for the
    contract. A change to this shape must bump `schema_version`.
    """

    schema_version = serializers.IntegerField(
        help_text=f"Shape of this document; currently {RULES_SUMMARY_SCHEMA_VERSION}."
    )
    campaign = RulesSummaryCampaignSerializer()
    profile_version = serializers.IntegerField(help_text="The CampaignProfile version used.")
    offer = serializers.CharField()
    targeting = RulesSummaryTargetingSerializer()
    exclusions = RulesSummaryExclusionsSerializer()
    buyers = RulesSummaryBuyersSerializer()
    outreach = RulesSummaryOutreachSerializer()
    custom_rules = serializers.CharField(allow_blank=True)
    structured_rules = serializers.ListField(
        child=serializers.DictField(),
        help_text="Reserved for a future machine-readable rule list; always empty in "
        "schema_version 1.",
    )
