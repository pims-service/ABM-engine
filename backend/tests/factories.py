"""factory_boy factories. Add one per model; import them from here in tests and fixtures."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import factory
from django.contrib.auth.hashers import make_password

from apps.accounts.models import User
from apps.campaigns.models import Campaign, Client
from apps.campaigns.services import create_campaign
from apps.companies.models import Company, CompanyResearch, DataSource, DataSourceType

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


# ------------------------------------------------------------------ companies (issue #40)


class DataSourceFactory(factory.django.DjangoModelFactory):
    """A manual data source by default. Override `type`/`url` for other kinds."""

    class Meta:
        model = DataSource

    client = factory.SubFactory(ClientFactory)
    type = DataSourceType.MANUAL
    name = factory.Sequence(lambda n: f"Source {n}")


class CompanyFactory(factory.django.DjangoModelFactory):
    """A company straight through the model (no dedupe). Use `create_company` to test that."""

    class Meta:
        model = Company

    campaign = factory.SubFactory(CampaignFactory)
    name = factory.Sequence(lambda n: f"Company {n}")
    website = factory.Sequence(lambda n: f"https://www.company{n}.example.com/about")
    country = "SA"


class CompanyResearchFactory(factory.django.DjangoModelFactory):
    """A snapshot; the data source defaults to a manual one for the company's client."""

    class Meta:
        model = CompanyResearch

    company = factory.SubFactory(CompanyFactory)
    data_source = factory.LazyAttribute(
        lambda o: DataSourceFactory(client=o.company.campaign.client)
    )
    industry = "Logistics"
    description = "Regional freight forwarder."
    employee_count = 250
    classification = "b2b"


def make_company(**overrides: Any) -> Company:
    return cast(Company, CompanyFactory(**overrides))


def make_data_source(**overrides: Any) -> DataSource:
    return cast(DataSource, DataSourceFactory(**overrides))


def make_research(**overrides: Any) -> CompanyResearch:
    return cast(CompanyResearch, CompanyResearchFactory(**overrides))


# ------------------------------------------------------------------ signals, contacts (#41)


class SignalFactory(factory.django.DjangoModelFactory):
    """A funding signal with a manual source of the company's client (no expiry rule yet)."""

    class Meta:
        model = "research.Signal"

    company = factory.SubFactory(CompanyFactory)
    data_source = factory.LazyAttribute(
        lambda o: DataSourceFactory(client=o.company.campaign.client)
    )
    type = "funding"
    evidence = "Raised a Series B round."
    event_date = factory.LazyFunction(lambda: date(2026, 3, 1))


class ContactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "companies.Contact"

    company = factory.SubFactory(CompanyFactory)
    data_source = factory.LazyAttribute(
        lambda o: DataSourceFactory(client=o.company.campaign.client)
    )
    name = factory.Sequence(lambda n: f"Contact {n}")
    title = "Head of IT"
    relevance_reason = "Owns the IT budget."


def make_signal(**overrides: Any) -> Any:
    return SignalFactory(**overrides)


def make_contact(**overrides: Any) -> Any:
    return ContactFactory(**overrides)


# ------------------------------------------------------------------ outreach (#43)


def allow_outreach(company: Company) -> str:
    """Decision lookup stub for tests: the human said `add` (HumanDecision comes from #42)."""
    return "add"


class OutreachAngleFactory(factory.django.DjangoModelFactory):
    """Goes through `create_angle`. Pass `signals=[...]` / `data_sources=[...]` to cite evidence."""

    class Meta:
        model = "outreach.OutreachAngle"

    company = factory.SubFactory(CompanyFactory)
    angle = "Support the existing business development team"
    rationale = "The company is hiring sales staff."

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        from apps.outreach.services import create_angle

        company, angle = kwargs.pop("company"), kwargs.pop("angle")
        return create_angle(company, angle, kwargs.pop("rationale"), **kwargs)


class MessageFactory(factory.django.DjangoModelFactory):
    """A draft email through `create_message`, with the decision lookup stubbed to `add`."""

    class Meta:
        model = "outreach.Message"

    angle = factory.SubFactory(OutreachAngleFactory)
    contact = factory.LazyAttribute(lambda o: ContactFactory(company=o.angle.company))
    channel = "email"
    language = "en"
    subject = "Quick question"
    body = "Hello, we help logistics teams with managed cloud security."

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        from apps.outreach.services import create_message

        kwargs.setdefault("decision_lookup", allow_outreach)
        angle, contact = kwargs.pop("angle"), kwargs.pop("contact")
        channel, language = kwargs.pop("channel"), kwargs.pop("language")
        return create_message(angle, contact, channel, language, kwargs.pop("body"), **kwargs)


def make_angle(**overrides: Any) -> Any:
    return OutreachAngleFactory(**overrides)


def make_message(**overrides: Any) -> Any:
    return MessageFactory(**overrides)
