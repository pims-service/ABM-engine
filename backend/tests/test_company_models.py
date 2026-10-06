"""Company, CompanyResearch and DataSource: history, latest, dedupe, tenancy (issue #40)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Model, ProtectedError
from django.urls import reverse

from apps.companies import services
from apps.companies.models import (
    Classification,
    Company,
    CompanyResearch,
    CompanyStatus,
    DataSource,
    DataSourceType,
    InputSource,
)
from apps.core.base import ImmutableRecordError, TenantMismatchError
from tests.factories import (
    make_campaign,
    make_client,
    make_company,
    make_data_source,
    make_research,
    make_user,
)

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _violates(obj) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        obj.save()


# ------------------------------------------------------------------ DataSource


class TestDataSource:
    def test_create_via_service(self):
        client = make_client()
        user = make_user()
        source = services.create_data_source(
            client,
            DataSourceType.NEWS,
            " Arab News ",
            url="https://arabnews.example/a",
            evidence_date=date(2026, 3, 1),
            user=user,
        )
        assert source.client_id == client.pk
        assert source.name == "Arab News"
        assert source.retrieved_at
        assert source.evidence_date == date(2026, 3, 1)
        assert source.created_by == user

    def test_manual_and_provider_need_no_url_but_website_and_news_do(self):
        client = make_client()
        for kind in (DataSourceType.MANUAL, DataSourceType.PROVIDER):
            assert services.create_data_source(client, kind, "x").url == ""
        for kind in (DataSourceType.WEBSITE, DataSourceType.NEWS):
            with pytest.raises(ValidationError):
                services.create_data_source(client, kind, "x")

    def test_url_rule_is_enforced_by_the_database(self):
        _violates(DataSource(client=make_client(), type="website", name="x"))

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            services.create_data_source(make_client(), "rumour", "x")
        _violates(DataSource(client=make_client(), type="rumour", name="x"))

    def test_append_only(self):
        source = make_data_source()
        source.name = "changed"
        with pytest.raises(ImmutableRecordError):
            source.save()
        with pytest.raises(ImmutableRecordError):
            source.delete()
        with pytest.raises(ImmutableRecordError):
            DataSource.objects.update(name="x")
        with pytest.raises(ImmutableRecordError):
            DataSource.objects.all().delete()

    def test_scoped_by_client(self):
        a, b = make_data_source(), make_data_source()
        admin = make_user(is_superuser=True)
        assert set(DataSource.objects.for_user(admin)) == {a, b}
        assert list(DataSource.objects.for_client(a.client)) == [a]
        assert not DataSource.objects.for_user(AnonymousUser()).exists()
        assert not DataSource.objects.for_user(make_user()).exists()


# ------------------------------------------------------------------ Company


class TestCompany:
    def test_defaults_and_client_copied_from_campaign(self):
        campaign = make_campaign()
        company = make_company(campaign=campaign, website="HTTPS://WWW.Acme.com/en")
        assert company.client_id == campaign.client_id
        assert company.domain == "acme.com"
        assert company.status == CompanyStatus.PENDING
        assert company.input_source == InputSource.MANUAL
        assert company.pk.version == 4
        assert company.archived_at is None
        assert str(company) == company.name

    def test_no_website_means_null_domain(self):
        company = make_company(website="")
        assert company.domain is None

    def test_domain_recomputed_when_website_changes(self):
        company = make_company(website="https://old.example.com")
        company.website = "https://www.new.example.com"
        company.save(update_fields=["website"])
        company.refresh_from_db()
        assert company.domain == "new.example.com"

    def test_domain_unique_per_campaign_even_when_archived(self):
        campaign = make_campaign()
        first = make_company(campaign=campaign, website="https://acme.com")
        _violates(Company(campaign=campaign, name="Other", website="http://www.ACME.com/x"))
        first.archive()
        _violates(Company(campaign=campaign, name="Other", website="acme.com"))

    def test_same_domain_allowed_in_other_campaign_or_client(self):
        campaign = make_campaign()
        make_company(campaign=campaign, website="https://acme.com")
        sibling = make_campaign(client=campaign.client)
        stranger = make_campaign()
        assert make_company(campaign=sibling, website="https://acme.com").domain == "acme.com"
        assert make_company(campaign=stranger, website="https://acme.com").domain == "acme.com"

    def test_many_companies_without_domain_allowed(self):
        campaign = make_campaign()
        make_company(campaign=campaign, name="A", website="")
        make_company(campaign=campaign, name="A", website="")
        assert Company.objects.filter(campaign=campaign, domain__isnull=True).count() == 2

    def test_empty_name_and_bad_enums_rejected_by_database(self):
        campaign = make_campaign()
        _violates(Company(campaign=campaign, name=""))
        _violates(Company(campaign=campaign, name="x", status="weird"))
        _violates(Company(campaign=campaign, name="x", input_source="weird"))

    def test_clean_validates_website_and_country(self):
        campaign = make_campaign()
        with pytest.raises(ValidationError) as exc:
            Company(campaign=campaign, name="x", website="localhost", country="sa").clean()
        assert "website" in exc.value.message_dict
        with pytest.raises(ValidationError) as exc:
            Company(campaign=campaign, name="x", country="sa").clean()
        assert "country" in exc.value.message_dict
        Company(campaign=campaign, name="x", website="acme.com", country="SA").clean()
        Company(campaign=campaign, name="x").clean()

    def test_client_cannot_diverge_from_campaign_or_move(self):
        campaign = make_campaign()
        with pytest.raises(TenantMismatchError):
            Company(campaign=campaign, client=make_client(), name="x").save()
        company = make_company(campaign=campaign)
        company.client = make_client()
        with pytest.raises(TenantMismatchError):
            company.save()

    def test_archive_and_restore_do_not_touch_status(self):
        company = make_company(status=CompanyStatus.ANALYZED)
        company.archive()
        assert company.is_archived
        assert company.status == CompanyStatus.ANALYZED
        assert company not in Company.objects.active()
        assert company in Company.objects.archived()
        company.restore()
        assert company.archived_at is None

    def test_scoping(self):
        a, b = make_company(), make_company()
        admin = make_user(is_superuser=True)
        assert set(Company.objects.for_user(admin)) == {a, b}
        assert list(Company.objects.for_client(a.client_id)) == [a]
        assert not Company.objects.for_user(make_user()).exists()

    def test_named_is_case_insensitive(self):
        company = make_company(name="Acme Logistics")
        assert list(Company.objects.named("  acme LOGISTICS ")) == [company]
        assert not Company.objects.named("Acme").exists()

    def test_protected_from_deletion_while_it_has_research(self):
        research = make_research()
        with pytest.raises(ProtectedError):
            research.company.delete()


# ------------------------------------------------------------------ create_company


class TestCreateCompany:
    def test_creates_with_normalized_domain(self):
        campaign = make_campaign()
        user = make_user()
        result = services.create_company(
            campaign,
            "  Acme  ",
            "https://www.Acme.com/",
            profile_url="https://linkedin.example/acme",
            country="sa",
            input_source=InputSource.CSV,
            user=user,
        )
        company = result.company
        assert result.created
        assert not result.duplicate
        assert not result.restored
        assert result.warnings == []
        assert company.name == "Acme"
        assert company.domain == "acme.com"
        assert company.country == "SA"
        assert company.input_source == InputSource.CSV
        assert company.client_id == campaign.client_id
        assert company.created_by == user

    def test_duplicate_domain_is_skipped_and_returns_existing(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme", "https://acme.com").company
        again = services.create_company(campaign, "Acme Inc", "http://www.acme.com/contact")
        assert again.duplicate
        assert not again.created
        assert not again.restored
        assert again.company == first
        assert Company.objects.filter(campaign=campaign).count() == 1
        first.refresh_from_db()
        assert first.name == "Acme"  # the existing row is not overwritten

    def test_duplicate_of_archived_company_restores_it(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme", "https://acme.com").company
        first.archive()
        again = services.create_company(campaign, "Acme", "acme.com")
        assert again.duplicate
        assert again.restored
        assert again.company.pk == first.pk
        again.company.refresh_from_db()
        assert again.company.archived_at is None
        assert Company.objects.filter(campaign=campaign).count() == 1

    def test_same_domain_other_campaign_is_not_a_duplicate(self):
        client = make_client()
        one, two = make_campaign(client=client), make_campaign(client=client)
        services.create_company(one, "Acme", "acme.com")
        result = services.create_company(two, "Acme", "acme.com")
        assert result.created
        assert Company.objects.filter(client=client, domain="acme.com").count() == 2

    def test_no_domain_warns_on_same_name_but_still_creates(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme Logistics").company
        result = services.create_company(campaign, "acme logistics")
        assert result.created
        assert result.company != first
        assert result.similar == [first]
        assert result.warnings
        assert Company.objects.filter(campaign=campaign).count() == 2

    def test_same_name_warning_ignores_archived_and_other_campaigns(self):
        campaign = make_campaign()
        old = services.create_company(campaign, "Acme").company
        old.archive()
        services.create_company(make_campaign(), "Acme")
        assert services.create_company(campaign, "Acme").similar == []

    def test_company_with_domain_gets_no_name_warning(self):
        campaign = make_campaign()
        services.create_company(campaign, "Acme")
        result = services.create_company(campaign, "Acme", "acme.com")
        assert result.created
        assert result.similar == []

    def test_invalid_input_rejected(self):
        campaign = make_campaign()
        with pytest.raises(ValidationError):
            services.create_company(campaign, "   ")
        with pytest.raises(ValidationError):
            services.create_company(campaign, "Acme", "localhost")
        with pytest.raises(ValidationError):
            services.create_company(campaign, "Acme", "acme.com", country="Saudi")
        with pytest.raises(ValidationError):
            services.create_company(campaign, "Acme", "acme.com", input_source="telepathy")
        assert not Company.objects.exists()

    def test_archived_campaign_takes_no_companies(self):
        campaign = make_campaign()
        campaign.archive()
        with pytest.raises(ValidationError):
            services.create_company(campaign, "Acme", "acme.com")

    def test_race_on_unique_domain_returns_the_winner(self, monkeypatch):
        campaign = make_campaign()
        winner = make_company(campaign=campaign, website="https://acme.com")
        # Simulate the lookup missing the row (another writer inserted it a moment later).
        calls = iter([None])
        real = services._find_by_domain
        monkeypatch.setattr(
            services,
            "_find_by_domain",
            lambda c, d: next(calls, None) or real(c, d),
        )
        result = services.create_company(campaign, "Acme", "acme.com")
        assert result.duplicate
        assert result.company == winner

    def test_integrity_error_without_domain_propagates(self, monkeypatch):
        campaign = make_campaign()

        def boom(self, *a, **k):
            raise IntegrityError("boom")

        monkeypatch.setattr(Company, "save", boom)
        with pytest.raises(IntegrityError):
            services.create_company(campaign, "Acme")

    def test_integrity_error_with_vanished_winner_propagates(self, monkeypatch):
        campaign = make_campaign()

        def boom(self, *a, **k):
            raise IntegrityError("boom")

        monkeypatch.setattr(Company, "save", boom)
        with pytest.raises(IntegrityError):
            services.create_company(campaign, "Acme", "acme.com")

    def test_job_id_recorded(self):
        import uuid

        job_id = uuid.uuid4()
        result = services.create_company(make_campaign(), "A", "a.com", created_by_job_id=job_id)
        assert result.company.created_by_job_id == job_id


# ------------------------------------------------------------------ CompanyResearch


class TestResearchHistory:
    def test_two_snapshots_are_two_rows(self):
        company = make_company()
        source = make_data_source(client=company.client)
        first = services.add_research_snapshot(
            company, source, researched_at=T0, employee_count=100
        )
        second = services.add_research_snapshot(
            company, source, researched_at=T0 + timedelta(days=30), employee_count=120
        )
        assert first.pk != second.pk
        assert company.research.count() == 2
        first.refresh_from_db()
        assert first.employee_count == 100  # the old snapshot is untouched

    def test_latest_for_returns_newest_researched_at(self):
        company = make_company()
        source = make_data_source(client=company.client)
        services.add_research_snapshot(company, source, researched_at=T0, employee_count=1)
        newest = services.add_research_snapshot(
            company, source, researched_at=T0 + timedelta(days=2), employee_count=3
        )
        services.add_research_snapshot(
            company, source, researched_at=T0 + timedelta(days=1), employee_count=2
        )
        assert CompanyResearch.objects.latest_for(company) == newest
        assert CompanyResearch.objects.latest_for(company.pk) == newest
        assert company.latest_research == newest

    def test_backfilled_older_snapshot_does_not_become_current(self):
        company = make_company()
        source = make_data_source(client=company.client)
        current = services.add_research_snapshot(company, source, researched_at=T0)
        services.add_research_snapshot(company, source, researched_at=T0 - timedelta(days=400))
        assert company.latest_research == current

    def test_tie_on_researched_at_goes_to_the_later_insert(self):
        company = make_company()
        source = make_data_source(client=company.client)
        services.add_research_snapshot(company, source, researched_at=T0, employee_count=1)
        later = services.add_research_snapshot(company, source, researched_at=T0, employee_count=2)
        assert company.latest_research == later

    def test_latest_for_without_research_is_none(self):
        assert make_company().latest_research is None

    def test_latest_is_per_company(self):
        a, b = make_company(), make_company()
        ra = make_research(company=a)
        rb = make_research(company=b)
        assert a.latest_research == ra
        assert b.latest_research == rb

    def test_current_returns_only_latest_per_company(self):
        a, b, empty = make_company(), make_company(), make_company()
        make_research(company=a, researched_at=T0)
        a_new = make_research(company=a, researched_at=T0 + timedelta(days=1))
        b_only = make_research(company=b, researched_at=T0)
        tie_first = make_research(company=empty, researched_at=T0)
        tie_last = make_research(company=empty, researched_at=T0)
        current = set(CompanyResearch.objects.current())
        assert current == {a_new, b_only, tie_last}
        assert tie_first not in current
        assert next(iter(CompanyResearch.objects.filter(company=a).latest_first())) == a_new

    def test_default_researched_at_is_the_source_retrieval_time(self):
        company = make_company()
        source = make_data_source(client=company.client, retrieved_at=T0)
        assert services.add_research_snapshot(company, source).researched_at == T0

    def test_all_brief_fields_round_trip(self):
        company = make_company()
        source = make_data_source(client=company.client)
        snap = services.add_research_snapshot(
            company,
            source,
            industry="Logistics",
            description="Freight",
            headquarters="Riyadh, SA",
            employee_count=500,
            business_model="Contract logistics",
            classification=Classification.BOTH,
            products_services="Freight, warehousing",
            target_customers="Retailers",
            sales_headcount=20,
            bd_headcount=5,
            marketing_headcount=7,
            commercial_partnerships_headcount=2,
            department_growth={"sales": 12.5},
            headcount_change_3m=Decimal("-12.00"),
            headcount_change_6m=Decimal("3.5"),
            headcount_change_12m=Decimal("40"),
        )
        snap.refresh_from_db()
        assert snap.headquarters == "Riyadh, SA"
        assert snap.classification == "both"
        assert (snap.sales_headcount, snap.bd_headcount) == (20, 5)
        assert (snap.marketing_headcount, snap.commercial_partnerships_headcount) == (7, 2)
        assert snap.department_growth == {"sales": 12.5}
        assert snap.headcount_change_3m == Decimal("-12.00")
        assert snap.headcount_change_6m == Decimal("3.50")
        assert snap.headcount_change_12m == Decimal("40.00")
        assert snap.created_at
        assert snap.pk.version == 4
        assert str(snap)

    def test_facts_default_to_null_not_guesses(self):
        company = make_company()
        snap = services.add_research_snapshot(company, make_data_source(client=company.client))
        for name in services.RESEARCH_FIELDS:
            assert getattr(snap, name) is None

    def test_validation(self):
        company = make_company()
        source = make_data_source(client=company.client)
        with pytest.raises(ValidationError):
            services.add_research_snapshot(company, source, employee_count=-1)
        with pytest.raises(ValidationError):
            services.add_research_snapshot(company, source, classification="huge")
        with pytest.raises(ValidationError):
            services.add_research_snapshot(company, source, headcount_change_3m=Decimal("12345"))
        with pytest.raises(ValidationError):
            services.add_research_snapshot(company, source, is_current=True)
        assert not CompanyResearch.objects.exists()

    def test_no_is_current_column(self):
        assert "is_current" not in {f.name for f in CompanyResearch._meta.get_fields()}

    def test_archived_company_is_read_only(self):
        company = make_company()
        source = make_data_source(client=company.client)
        company.archive()
        with pytest.raises(ValidationError):
            services.add_research_snapshot(company, source)

    def test_classification_checked_by_database(self):
        company = make_company()
        source = make_data_source(client=company.client)
        _violates(CompanyResearch(company=company, data_source=source, classification="huge"))

    def test_append_only(self):
        snap = make_research()
        snap.industry = "Retail"
        with pytest.raises(ImmutableRecordError):
            snap.save()
        with pytest.raises(ImmutableRecordError):
            snap.delete()
        with pytest.raises(ImmutableRecordError):
            CompanyResearch.objects.update(industry="x")
        with pytest.raises(ImmutableRecordError):
            CompanyResearch.objects.all().delete()
        with pytest.raises(ImmutableRecordError):
            CompanyResearch.objects.bulk_update([snap], ["industry"])
        snap.refresh_from_db()
        assert snap.industry == "Logistics"

    def test_data_source_is_protected(self):
        snap = make_research()
        # Bypass the append-only guard on DataSource to prove the FK itself is PROTECT.
        with pytest.raises(ProtectedError):
            Model.delete(snap.data_source)


# ------------------------------------------------------------------ tenancy consistency


class TestTenantConsistency:
    def test_research_client_copied_from_company(self):
        research = make_research()
        assert research.client_id == research.company.client_id == research.data_source.client_id

    def test_source_from_another_client_is_rejected_by_service_and_model(self):
        company = make_company()
        foreign = make_data_source()  # its own, different client
        with pytest.raises(ValidationError):
            services.add_research_snapshot(company, foreign)
        with pytest.raises(TenantMismatchError):
            CompanyResearch(company=company, data_source=foreign).save()
        assert not CompanyResearch.objects.exists()

    def test_research_cannot_carry_a_different_client_than_its_company(self):
        company = make_company()
        source = make_data_source(client=company.client)
        with pytest.raises(TenantMismatchError):
            CompanyResearch(company=company, data_source=source, client=make_client()).save()

    def test_every_row_matches_its_parents_client(self):
        """The #53 walk for this app: child.client_id == parent.client_id along every FK."""
        for _ in range(3):
            make_research()
        for company in Company.objects.select_related("campaign"):
            assert company.client_id == company.campaign.client_id
        for research in CompanyResearch.objects.select_related("company", "data_source"):
            assert research.client_id == research.company.client_id
            assert research.client_id == research.data_source.client_id

    def test_two_clients_coexist_without_leaking(self):
        a, b = make_research(), make_research()
        admin = make_user(is_superuser=True)
        assert set(CompanyResearch.objects.for_user(admin)) == {a, b}
        assert list(CompanyResearch.objects.for_client(a.client)) == [a]
        assert list(Company.objects.for_client(b.client)) == [b.company]
        assert not CompanyResearch.objects.for_user(make_user()).exists()
        assert not CompanyResearch.objects.for_user(AnonymousUser()).exists()


# ------------------------------------------------------------------ admin


class TestAdmin:
    def test_models_registered_and_read_only(self, client):
        admin_user = make_user(is_superuser=True, is_staff=True)
        client.force_login(admin_user)
        research = make_research()
        for model, obj in (
            (CompanyResearch, research),
            (DataSource, research.data_source),
            (Company, research.company),
        ):
            assert model in site._registry
            name = model._meta.model_name
            assert client.get(reverse(f"admin:companies_{name}_changelist")).status_code == 200
            assert (
                client.get(reverse(f"admin:companies_{name}_change", args=[obj.pk])).status_code
                == 200
            )
            assert client.get(reverse(f"admin:companies_{name}_add")).status_code == 403
            assert (
                client.get(reverse(f"admin:companies_{name}_delete", args=[obj.pk])).status_code
                == 403
            )

    def test_archive_and_restore_actions(self, client):
        client.force_login(make_user(is_superuser=True, is_staff=True))
        company = make_company()
        url = reverse("admin:companies_company_changelist")
        client.post(url, {"action": "archive_selected", "_selected_action": [company.pk]})
        company.refresh_from_db()
        assert company.is_archived
        client.post(url, {"action": "restore_selected", "_selected_action": [company.pk]})
        company.refresh_from_db()
        assert not company.is_archived

    def test_research_admin_forbids_changes(self, rf):
        model_admin = site._registry[CompanyResearch]
        request = rf.get("/")
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)
        assert "industry" in model_admin.get_readonly_fields(request)
