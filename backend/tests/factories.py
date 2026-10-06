"""factory_boy factories. Add one per model; import them from here in tests and fixtures."""

from __future__ import annotations

from typing import Any, cast

import factory
from django.contrib.auth.hashers import make_password

from apps.accounts.models import User
from apps.campaigns.models import Campaign, Client
from apps.campaigns.services import create_campaign

DEFAULT_PASSWORD = "test-password-123"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    name = factory.Sequence(lambda n: f"User {n}")
    # Hashed at build time (test settings use a fast hasher), so no post-generation save needed.
    password = factory.LazyFunction(lambda: make_password(DEFAULT_PASSWORD))


def make_user(**overrides: Any) -> User:
    """Create a persisted user. factory_boy is untyped, so this gives callers a real `User`."""
    return cast(User, UserFactory(**overrides))


# ------------------------------------------------------------------ tenancy (issue #39)


class ClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Client

    name = factory.Sequence(lambda n: f"Client {n}")
    notes = ""


class CampaignFactory(factory.django.DjangoModelFactory):
    """Builds a campaign with its version 1 profile through `services.create_campaign`.

    Override rule fields with `profile__offer="..."`, `profile__countries=["SA"]`, etc.
    """

    class Meta:
        model = Campaign

    client = factory.SubFactory(ClientFactory)
    name = factory.Sequence(lambda n: f"Campaign {n}")
    created_by = None
    profile = factory.Dict(
        {
            "offer": "Managed cloud security for mid-size companies",
            "countries": ["SA", "AE"],
            "industries": ["Logistics", "Retail"],
            "company_size_min": 50,
            "company_size_max": 500,
            "business_model": "b2b",
            "excluded_industries": ["Government"],
            "excluded_company_types": ["non-profit"],
            "target_departments": ["IT", "Operations"],
            "preferred_buyer_titles": ["CIO", "Head of IT", "IT Manager"],
            "outreach_languages": ["en", "ar"],
            "custom_rules": "Prefer companies with their own warehouses.",
        }
    )

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return create_campaign(
            kwargs["client"], kwargs["name"], kwargs["profile"], kwargs["created_by"]
        )


def make_client(**overrides: Any) -> Client:
    return cast(Client, ClientFactory(**overrides))


def make_campaign(**overrides: Any) -> Campaign:
    """Persisted campaign with version 1 (`campaign.current_profile`)."""
    return cast(Campaign, CampaignFactory(**overrides))
