"""Sample (fake) data for local development: two clients, users per role, companies (issue #52).

Everything here is clearly marked as sample data: names carry "(sample)", notes and data-source
names start with :data:`SAMPLE_MARKER`, websites and emails live on ``example.com`` (reserved,
never delivered) and no real person is named. Rows are created through the same services the
API uses (audit entries included) and every seeder is idempotent: a second run creates nothing
and never edits what a developer changed afterwards.

Passwords: ``DEV_SEED_USER_PASSWORD`` (environment only). Without it the sample users get an
unusable password and cannot log in; the dev superuser seeder covers sign-in.

Extension point: assessments (#42) and outreach (#43) are not seeded yet. Add a function with
the :data:`apps.core.seeding.Seeder` signature that runs after :func:`seed_sample_companies`
and append it to ``SEEDERS`` in ``apps/core/seeding.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, Client, ClientMembership
from apps.campaigns.services import activate_campaign, create_campaign, create_client
from apps.companies.models import Company, Contact, ContactRole, DataSource, DataSourceType
from apps.companies.services import (
    add_research_snapshot,
    create_company,
    create_contact,
    create_data_source,
)
from apps.core.roles import Role
from apps.research.models import Signal, SignalType
from apps.research.services import create_signal

SAMPLE_MARKER = "[SAMPLE DATA]"
SEED_PASSWORD_ENV = "DEV_SEED_USER_PASSWORD"  # noqa: S105  # pragma: allowlist secret


@dataclass(frozen=True)
class SeedCounts:
    created: int = 0
    existing: int = 0

    def add(self, created: bool) -> SeedCounts:
        return SeedCounts(self.created + created, self.existing + (not created))


# ------------------------------------------------------------------ specs

SKYLIGHT = "SkyLight"
MERIDIAN = "Meridian Labs"

# Brief section 3 (SkyLight) and a deliberately different second client.
CLIENTS: dict[str, dict[str, Any]] = {
    "skylight": {
        "name": SKYLIGHT,
        "notes": f"{SAMPLE_MARKER} Brief section 3 example client.",
        "campaign": "SkyLight KSA outbound",
        "profile": {
            "offer": "B2B Outbound / Lead Generation",
            "countries": ["SA"],
            "industries": ["Financial Services", "Accounting", "SaaS", "Technology"],
            "company_size_min": 10,
            "company_size_max": 500,
            "business_model": "b2b",
            "excluded_industries": [],
            "excluded_company_types": [],
            "target_departments": ["Sales", "Business Development", "Commercial", "Partnerships"],
            "preferred_buyer_titles": [
                "VP BD",
                "Head of BD",
                "Commercial Director",
                "Sales Director",
                "CEO",
                "Founder",
                "Managing Director",
            ],
            "outreach_languages": ["ar", "en"],
            "custom_rules": (
                f"{SAMPLE_MARKER} A company does not need a trigger to qualify: "
                "strong ICP with no trigger is still eligible."
            ),
            "change_note": "Initial profile from Brief section 3.",
        },
    },
    "meridian": {
        "name": MERIDIAN,
        "notes": f"{SAMPLE_MARKER} Second client with different ICP rules (multi-client proof).",
        "campaign": "Meridian UAE cloud security",
        "profile": {
            "offer": "Managed cloud security for mid-size companies",
            "countries": ["AE"],
            "industries": ["Logistics", "Retail", "Healthcare"],
            "company_size_min": 100,
            "company_size_max": 1000,
            "business_model": "both",
            "excluded_industries": ["Government", "Education"],
            "excluded_company_types": ["non-profit", "sole proprietorship"],
            "target_departments": ["IT", "Security", "Operations"],
            "preferred_buyer_titles": ["CIO", "CISO", "Head of IT", "IT Director"],
            "outreach_languages": ["en"],
            "custom_rules": f"{SAMPLE_MARKER} Prefer companies that run their own warehouses.",
            "change_note": "Initial sample profile.",
        },
    },
}

# (key, client key, role, display name). Emails: seed-<key>@<client>.example.com
USERS: tuple[tuple[str, str, str], ...] = (
    ("skylight_admin", "skylight", Role.ADMIN),
    ("skylight_manager", "skylight", Role.MANAGER),
    ("skylight_reviewer", "skylight", Role.REVIEWER),
    ("skylight_viewer", "skylight", Role.VIEWER),
    ("meridian_admin", "meridian", Role.ADMIN),
    ("meridian_viewer", "meridian", Role.VIEWER),
)


def user_email(key: str) -> str:
    client_key, _, role = key.partition("_")
    return f"seed-{role}@{client_key}.example.com"


def _contact(name: str, title: str, role: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": f"{name} {SAMPLE_MARKER}",
        "title": title,
        "role": role,
        "reason": reason,
        **extra,
    }


# Fresh signal: no expiry rule yet (null counts as fresh), so it never goes stale in a dev DB.
# Expired signal: the evidence expired in the past, so Trigger is "No" today.
COMPANIES: tuple[dict[str, Any], ...] = (
    {
        "key": "tiqmo",
        "client": "skylight",
        "name": "tiqmo (sample)",
        "website": "https://www.tiqmo.example.com",
        "country": "SA",
        "research": {
            "industry": "Financial Services",
            "description": "Sample digital wallet and payments company (Brief section 3 example).",
            "headquarters": "Riyadh, Saudi Arabia",
            "employee_count": 155,
            "classification": "b2b",
            "bd_headcount": 15,
            "headcount_change_12m": Decimal("-12.00"),
        },
        "signals": [],  # strong ICP, no current trigger: still eligible
        "contacts": [
            _contact("Sample CEO", "CEO", ContactRole.PRIMARY, "Owns commercial strategy."),
            _contact("Sample BD Head", "Head of BD", ContactRole.SECONDARY, "Runs the BD team."),
        ],
    },
    {
        "key": "najm",
        "client": "skylight",
        "name": "Najm Ledger (sample)",
        "website": "https://www.najm-ledger.example.com",
        "country": "SA",
        "research": {
            "industry": "SaaS",
            "description": "Sample accounting SaaS for small businesses.",
            "headquarters": "Jeddah, Saudi Arabia",
            "employee_count": 80,
            "classification": "b2b",
            "sales_headcount": 6,
            "headcount_change_12m": Decimal("18.50"),
        },
        "signals": [
            {
                "type": SignalType.SALES_HIRING,
                "evidence": "Sample job post: Senior Sales Executive, Jeddah.",
                "days_ago": 14,
                "expires": None,
                "source": "https://jobs.example.com/najm-ledger/senior-sales-executive",
            }
        ],
        "contacts": [
            _contact(
                "Sample Sales Director", "Sales Director", ContactRole.PRIMARY, "Hires sales."
            ),
        ],
    },
    {
        "key": "rimal",
        "client": "skylight",
        "name": "Rimal Accounting (sample)",
        "website": "https://www.rimal-accounting.example.com",
        "country": "SA",
        "research": {
            "industry": "Accounting",
            "description": "Sample mid-size accounting firm.",
            "headquarters": "Riyadh, Saudi Arabia",
            "employee_count": 40,
            "classification": "b2b",
            "commercial_partnerships_headcount": 2,
        },
        "signals": [
            {
                "type": SignalType.NEW_LEADERSHIP,
                "evidence": "Sample news: new Managing Director appointed (now expired).",
                "event": date(2025, 4, 1),
                "expires": datetime(2025, 7, 1, tzinfo=UTC),
                "source": "https://news.example.com/rimal-accounting/new-md",
            }
        ],
        "contacts": [
            _contact(
                "Sample Managing Director",
                "Managing Director",
                ContactRole.PRIMARY,
                "Final decision maker.",
            ),
        ],
    },
    {
        "key": "gulf_freight",
        "client": "meridian",
        "name": "Gulf Freight Tech (sample)",
        "website": "https://www.gulf-freight.example.com",
        "country": "AE",
        "research": {
            "industry": "Logistics",
            "description": "Sample freight forwarder with its own warehouses.",
            "headquarters": "Dubai, United Arab Emirates",
            "employee_count": 420,
            "classification": "b2b",
            "headcount_change_12m": Decimal("9.00"),
        },
        "signals": [
            {
                "type": SignalType.FUNDING,
                "evidence": "Sample news: Series B funding round announced.",
                "days_ago": 30,
                "expires": None,
                "source": "https://news.example.com/gulf-freight/series-b",
            }
        ],
        "contacts": [
            _contact("Sample CIO", "CIO", ContactRole.PRIMARY, "Owns IT and security budget."),
            _contact("Sample CISO", "CISO", ContactRole.SECONDARY, "Owns security decisions."),
        ],
    },
    {
        "key": "oasis_retail",
        "client": "meridian",
        "name": "Oasis Retail Group (sample)",
        "website": "https://www.oasis-retail.example.com",
        "country": "AE",
        "research": {
            "industry": "Retail",
            "description": "Sample retail group, B2C and B2B.",
            "headquarters": "Abu Dhabi, United Arab Emirates",
            "employee_count": 760,
            "classification": "both",
        },
        "signals": [],
        "contacts": [
            _contact("Sample IT Director", "IT Director", ContactRole.PRIMARY, "Runs IT."),
        ],
    },
    {
        "key": "dune_health",
        "client": "meridian",
        "name": "Dune Health Systems (sample)",
        "website": "https://www.dune-health.example.com",
        "country": "AE",
        "research": {
            "industry": "Healthcare",
            "description": "Sample private hospital operator.",
            "headquarters": "Sharjah, United Arab Emirates",
            "employee_count": 310,
            "classification": "b2c",
        },
        "signals": [
            {
                "type": SignalType.NEW_OFFICE,
                "evidence": "Sample news: new clinic opened (expired).",
                "event": date(2025, 2, 10),
                "expires": datetime(2025, 5, 10, tzinfo=UTC),
                "source": "https://news.example.com/dune-health/new-clinic",
            }
        ],
        "contacts": [],
    },
)


# ------------------------------------------------------------------ seeders


def seed_sample_users(environ: Mapping[str, str]) -> Any:
    """Four roles on SkyLight, two on Meridian. Existing users are never modified."""
    from apps.core.seeding import SeedResult  # local import: seeding imports this module

    password = environ.get(SEED_PASSWORD_ENV, "") or None
    counts = SeedCounts()
    user_model = get_user_model()
    for key, _client, role in USERS:
        email = user_email(key)
        if user_model._default_manager.filter(email=email).exists():
            counts = counts.add(False)
            continue
        user_model._default_manager.create_user(
            email=email, password=password, name=f"Sample {role.title()} {SAMPLE_MARKER}"
        )
        counts = counts.add(True)
    detail = "login enabled" if password else f"no login: {SEED_PASSWORD_ENV} is not set"
    return SeedResult("sample users", counts.created, counts.existing, detail)


def seed_sample_clients(environ: Mapping[str, str]) -> Any:
    """Clients, their campaigns (version 1 profile) and memberships, all through the services."""
    from apps.core.seeding import SeedResult

    users = {key: User.objects.filter(email=user_email(key)).first() for key, _, _ in USERS}
    counts = SeedCounts()
    for client_key, spec in CLIENTS.items():
        actor = users.get(f"{client_key}_admin")
        client = Client.objects.active().filter(name=spec["name"]).first()
        if client is None:
            client = create_client(spec["name"], spec["notes"], user=actor)
            counts = counts.add(True)
        else:
            counts = counts.add(False)

        campaign = Campaign.objects.active().filter(client=client, name=spec["campaign"]).first()
        if campaign is None:
            campaign = create_campaign(client, spec["campaign"], spec["profile"], user=actor)
            activate_campaign(campaign, user=actor)
            counts = counts.add(True)
        else:
            counts = counts.add(False)

        for key, member_client, role in USERS:
            user = users[key]
            if member_client != client_key or user is None:
                continue
            if ClientMembership.objects.filter(client=client, user=user).exists():
                counts = counts.add(False)
            else:
                grant_membership(client, user, role)
                counts = counts.add(True)
    return SeedResult("sample clients, campaigns, memberships", counts.created, counts.existing)


def _source(client: Client, type: str, name: str, url: str = "") -> tuple[DataSource, bool]:
    existing = DataSource.objects.filter(client=client, type=type, name=name, url=url).first()
    if existing is not None:
        return existing, False
    return create_data_source(client, type, name, url=url), True


def seed_sample_companies(environ: Mapping[str, str]) -> Any:
    """Companies with a research snapshot, signals, contacts and their data sources."""
    from apps.core.seeding import SeedResult

    counts = SeedCounts()
    now = timezone.now()
    for spec in COMPANIES:
        campaign = Campaign.objects.filter(
            client__name=CLIENTS[spec["client"]]["name"], name=CLIENTS[spec["client"]]["campaign"]
        ).first()
        if campaign is None:  # clients seeder did not run (or the campaign was renamed)
            continue
        client = campaign.client
        actor = User.objects.filter(email=user_email(f"{spec['client']}_admin")).first()

        result = create_company(
            campaign,
            spec["name"],
            spec["website"],
            country=spec["country"],
            input_source="manual",
            user=actor,
        )
        company = result.company
        counts = counts.add(result.created)

        provider, _ = _source(client, DataSourceType.PROVIDER, f"{SAMPLE_MARKER} provider export")
        if not company.research.exists():
            add_research_snapshot(company, provider, **spec["research"])
            counts = counts.add(True)
        else:
            counts = counts.add(False)

        for sig in spec["signals"]:
            source, _ = _source(
                client, DataSourceType.NEWS, f"{SAMPLE_MARKER} {sig['type']}", sig["source"]
            )
            if Signal.objects.filter(company=company, type=sig["type"]).exists():
                counts = counts.add(False)
                continue
            event = sig.get("event") or (now - timedelta(days=sig["days_ago"])).date()
            create_signal(
                company,
                source,
                sig["type"],
                f"{SAMPLE_MARKER} {sig['evidence']}",
                event,
                expires_at=sig["expires"],
                user=actor,
            )
            counts = counts.add(True)

        for index, contact in enumerate(spec["contacts"], start=1):
            if Contact.objects.filter(company=company, name=contact["name"]).exists():
                counts = counts.add(False)
                continue
            manual, _ = _source(client, DataSourceType.MANUAL, f"{SAMPLE_MARKER} manual entry")
            create_contact(
                company,
                manual,
                contact["name"],
                title=contact["title"],
                relevance_reason=contact["reason"],
                rank=index,
                role=contact["role"],
                user=actor,
            )
            counts = counts.add(True)
    return SeedResult("sample companies and evidence", counts.created, counts.existing)


# ------------------------------------------------------------------ lookup for tests and demos


@dataclass
class SeededWorld:
    """Handles to the sample data, keyed by the keys used in this module."""

    users: dict[str, User] = field(default_factory=dict)
    clients: dict[str, Client] = field(default_factory=dict)
    campaigns: dict[str, Campaign] = field(default_factory=dict)
    companies: dict[str, Company] = field(default_factory=dict)


def load_seeded_world() -> SeededWorld:
    """Read the sample data back from the database (after the seeders have run)."""
    world = SeededWorld()
    for key, _client, _role in USERS:
        world.users[key] = User.objects.get(email=user_email(key))
    for client_key, spec in CLIENTS.items():
        client = Client.objects.get(name=spec["name"])
        world.clients[client_key] = client
        world.campaigns[client_key] = Campaign.objects.get(client=client, name=spec["campaign"])
    for spec in COMPANIES:
        world.companies[spec["key"]] = Company.objects.get(
            campaign=world.campaigns[spec["client"]], name=spec["name"]
        )
    return world


# Re-exported for the registry in apps/core/seeding.py.
SAMPLE_SEEDERS = (seed_sample_users, seed_sample_clients, seed_sample_companies)
