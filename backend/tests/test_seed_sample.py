"""Sample seed data (issue #52): idempotency, counts, tenant consistency, DB constraints."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection

from apps.accounts.models import User
from apps.campaigns.models import Campaign, CampaignProfile, Client, ClientMembership
from apps.companies.models import Company, CompanyResearch, Contact, DataSource
from apps.core.models import AuditLog
from apps.core.roles import Level
from apps.core.seed_sample import SAMPLE_MARKER, SeededWorld
from apps.core.tenancy import has_client_level
from apps.research.models import Signal
from apps.research.services import company_trigger_state
from tests.fixtures_seed import SEEDED_PASSWORD, run_all_seeders

pytestmark = pytest.mark.django_db

TABLES = (
    Client,
    Campaign,
    CampaignProfile,
    ClientMembership,
    Company,
    CompanyResearch,
    DataSource,
    Signal,
    Contact,
    AuditLog,
    User,
)


def counts() -> dict[str, int]:
    return {m.__name__: m._default_manager.count() for m in TABLES}


def test_counts(seeded_world: SeededWorld):
    now = counts()
    assert now["Client"] == 2
    assert now["Campaign"] == 2
    assert now["CampaignProfile"] == 2
    assert now["ClientMembership"] == 6
    assert now["User"] == 6
    assert now["Company"] == 6
    assert now["CompanyResearch"] == 6
    assert now["Signal"] == 4
    assert now["Contact"] == 7
    assert now["AuditLog"] >= 4  # clients and campaigns went through the audited services


def test_second_run_creates_nothing(seeded_world: SeededWorld):
    before = counts()
    results = run_all_seeders()
    assert counts() == before
    assert all(r.created == 0 for r in results if r.name != "superuser")


def test_command_is_idempotent(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.setenv("DEV_SEED_USER_PASSWORD", SEEDED_PASSWORD)
    call_command("seed_dev_data", stdout=StringIO())
    before = counts()
    out = StringIO()
    call_command("seed_dev_data", stdout=out)
    assert counts() == before
    assert "sample companies and evidence: created=0" in out.getvalue()


def test_skylight_matches_brief(seeded_world: SeededWorld):
    profile = seeded_world.campaigns["skylight"].current_profile
    assert profile.version == 1
    assert profile.countries == ["SA"]
    assert profile.industries == ["Financial Services", "Accounting", "SaaS", "Technology"]
    assert (profile.company_size_min, profile.company_size_max) == (10, 500)
    assert profile.business_model == "b2b"
    assert profile.target_departments == [
        "Sales",
        "Business Development",
        "Commercial",
        "Partnerships",
    ]
    assert profile.preferred_buyer_titles[0] == "VP BD"
    assert profile.outreach_languages == ["ar", "en"]
    assert seeded_world.campaigns["skylight"].status == "active"


def test_clients_have_different_rules(seeded_world: SeededWorld):
    a = seeded_world.campaigns["skylight"].current_profile
    b = seeded_world.campaigns["meridian"].current_profile
    for name in (
        "offer",
        "countries",
        "industries",
        "preferred_buyer_titles",
        "outreach_languages",
    ):
        assert getattr(a, name) != getattr(b, name)


def test_tiqmo_example(seeded_world: SeededWorld):
    tiqmo = seeded_world.companies["tiqmo"]
    research = tiqmo.latest_research
    assert research is not None
    assert research.headquarters is not None
    assert research.headquarters.startswith("Riyadh")
    assert research.industry == "Financial Services"
    assert (research.employee_count, research.bd_headcount) == (155, 15)
    assert research.headcount_change_12m is not None
    assert float(research.headcount_change_12m) == -12.0
    assert company_trigger_state(tiqmo).label == "No"


def test_fresh_and_expired_signals(seeded_world: SeededWorld):
    fresh = company_trigger_state(seeded_world.companies["najm"])
    assert fresh.label == "Yes"
    signal = fresh.signals[0]
    assert signal.type == "sales_hiring"
    assert signal.event_date
    assert signal.data_source.url
    expired = seeded_world.companies["rimal"]
    assert Signal.objects.filter(company=expired).count() == 1
    assert company_trigger_state(expired).label == "No"


def test_everything_is_marked_sample(seeded_world: SeededWorld):
    assert all(SAMPLE_MARKER in c.notes for c in seeded_world.clients.values())
    assert all("(sample)" in c.name for c in seeded_world.companies.values())
    assert all(SAMPLE_MARKER in c.name for c in Contact.objects.all())
    assert all(SAMPLE_MARKER in s.name for s in DataSource.objects.all())
    assert all(c.website.endswith(".example.com") for c in seeded_world.companies.values())
    assert all(u.email.endswith(".example.com") for u in seeded_world.users.values())


def test_tenant_consistency(seeded_world: SeededWorld):
    for company in Company.objects.select_related("campaign"):
        assert company.client_id == company.campaign.client_id
    for model in (CompanyResearch, Signal, Contact):
        for row in model.objects.select_related("company", "data_source"):
            assert row.client_id == row.company.client_id == row.data_source.client_id
    for campaign in Campaign.objects.select_related("current_profile"):
        assert campaign.current_profile.campaign_id == campaign.pk
        assert campaign.client_id == campaign.current_profile.client_id
    assert Company.objects.for_client(seeded_world.clients["skylight"]).count() == 3
    assert Company.objects.for_client(seeded_world.clients["meridian"]).count() == 3


def test_memberships_and_isolation(seeded_world: SeededWorld):
    sky, mer = seeded_world.clients["skylight"].pk, seeded_world.clients["meridian"].pk
    users = seeded_world.users
    assert has_client_level(users["skylight_admin"], sky, Level.MANAGE)
    assert has_client_level(users["skylight_reviewer"], sky, Level.DECIDE)
    assert not has_client_level(users["skylight_viewer"], sky, Level.DECIDE)
    assert not has_client_level(users["skylight_admin"], mer, Level.READ)
    assert not has_client_level(users["meridian_viewer"], sky, Level.READ)
    assert {m.role for m in ClientMembership.objects.filter(client_id=sky)} == {
        "admin",
        "manager",
        "reviewer",
        "viewer",
    }


def test_users_cannot_log_in_without_env_password(db):
    run_all_seeders({"DEV_SEED_USER_PASSWORD": ""})
    user = User.objects.get(email="seed-admin@skylight.example.com")
    assert not user.has_usable_password()


def test_login_with_env_password(seeded_world: SeededWorld):
    assert seeded_world.users["skylight_admin"].check_password(SEEDED_PASSWORD)


def test_rows_satisfy_model_and_database_constraints(seeded_world: SeededWorld):
    if connection.vendor == "postgresql":  # deferred FKs (campaign -> profile) checked now
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for model in (Client, Campaign, CampaignProfile, Company, CompanyResearch, Contact, Signal):
        for obj in model.objects.all():
            obj.full_clean(validate_unique=True, validate_constraints=True)
