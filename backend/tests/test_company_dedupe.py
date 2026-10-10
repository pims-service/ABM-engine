"""Company duplicate detection and merge-safe upsert (issue #58).

Concurrency is in ``test_company_dedupe_concurrency.py`` (it needs committed data).
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.forms.models import model_to_dict
from django.utils import timezone

from apps.companies import services
from apps.companies.dedupe import (
    MAX_WEAK_CANDIDATES,
    NO_MATCH,
    MatchStrength,
    find_duplicate,
)
from apps.companies.models import Company
from apps.companies.normalize import MAX_PROFILE_KEY_LENGTH, name_match_key, profile_key
from apps.core.base import ImmutableRecordError, TenantMismatchError
from apps.imports import services as import_services
from apps.imports.models import ImportRow, ImportRowOutcome
from tests.factories import make_campaign, make_client, make_company
from tests.factories_imports import make_import_batch, make_import_row

pytestmark = pytest.mark.django_db


def _snapshot(company: Company) -> dict:
    """Every column of the row as stored, including the timestamps."""
    company.refresh_from_db()
    data = model_to_dict(company, exclude=[])
    data.update(
        {
            "updated_at": company.updated_at,
            "created_at": company.created_at,
            "archived_at": company.archived_at,
            "domain": company.domain,
            "profile_key": company.profile_key,
            "name_key": company.name_key,
        }
    )
    return data


# ------------------------------------------------------------------ keys stored on the row


class TestStoredKeys:
    def test_keys_are_derived_on_save(self):
        company = make_company(
            name="ACME  Trading L.L.C.",
            website="https://www.Acme.com/",
            profile_url="https://uk.linkedin.com/company/Acme-Trading/about?trk=x",
        )
        assert company.domain == "acme.com"
        assert company.profile_key == "linkedin:acme-trading"
        assert company.name_key == "acme trading"

    def test_no_profile_url_means_null_key(self):
        company = make_company(profile_url="")
        assert company.profile_key is None

    def test_update_fields_save_keeps_keys_in_step(self):
        company = make_company(name="Acme", profile_url="https://linkedin.com/company/one")
        company.profile_url = "https://linkedin.com/company/two"
        company.name = "Beta Inc"
        company.save(update_fields=["profile_url", "name"])
        company.refresh_from_db()
        assert company.profile_key == "linkedin:two"
        assert company.name_key == "beta"

    def test_very_long_generic_profile_url_is_hashed_into_a_bounded_key(self):
        url = "https://example.com/" + "a" * 1500
        key = profile_key(url)
        assert key is not None
        assert key.startswith("generic#")
        assert len(key) <= MAX_PROFILE_KEY_LENGTH
        assert profile_key(url) == key
        assert make_company(profile_url=url).profile_key == key

    def test_name_key_helper(self):
        assert name_match_key("The Acme Co.") == "acme"
        assert name_match_key("   ") == ""
        assert name_match_key(None) == ""

    def test_profile_key_is_unique_per_campaign_in_the_database(self):
        campaign = make_campaign()
        make_company(campaign=campaign, profile_url="https://www.linkedin.com/company/acme")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_company(campaign=campaign, profile_url="https://linkedin.com/company/ACME/")

    def test_same_profile_in_another_campaign_is_allowed(self):
        make_company(profile_url="https://www.linkedin.com/company/acme")
        make_company(profile_url="https://www.linkedin.com/company/acme")  # own campaign


# ------------------------------------------------------------------ find_duplicate


class TestFindDuplicate:
    def test_nothing_in_the_campaign_is_none(self):
        match = find_duplicate(make_campaign(), "Acme", "acme.com", "", "SA")
        assert match.strength is MatchStrength.NONE
        assert match.company is None
        assert match.matched_on is None
        assert match.candidates == ()
        assert match == NO_MATCH

    def test_empty_input_is_none(self):
        campaign = make_campaign()
        make_company(campaign=campaign)
        assert find_duplicate(campaign, "", "", "", "").strength is MatchStrength.NONE

    @pytest.mark.parametrize(
        "website",
        ["acme.com", "https://www.acme.com", "HTTP://ACME.COM/about?x=1", "www.acme.com/"],
    )
    def test_domain_match_is_strong(self, website):
        campaign = make_campaign()
        existing = make_company(campaign=campaign, name="Acme", website="https://acme.com")
        match = find_duplicate(campaign, "Totally other name", website, "", "")
        assert match.strength is MatchStrength.STRONG
        assert match.is_strong
        assert match.matched_on == "domain"
        assert match.company == existing
        assert match.candidates == (existing,)

    @pytest.mark.parametrize(
        "variant",
        [
            "https://www.linkedin.com/company/acme",
            "linkedin.com/company/Acme/",
            "https://uk.linkedin.com/company/ACME/about/?trk=public",
            "https://www.linkedin.com/company-beta/acme",
            "https://m.linkedin.com/en/company/acme/posts",
        ],
    )
    def test_linkedin_slug_variants_match_strongly(self, variant):
        campaign = make_campaign()
        existing = make_company(
            campaign=campaign,
            name="Acme",
            website="",
            profile_url="https://www.linkedin.com/company/acme",
        )
        match = find_duplicate(campaign, "Different", "", variant, "")
        assert match.strength is MatchStrength.STRONG
        assert match.matched_on == "profile"
        assert match.company == existing

    def test_other_linkedin_slug_is_not_a_match(self):
        campaign = make_campaign()
        make_company(campaign=campaign, name="Acme", profile_url="https://linkedin.com/company/a")
        match = find_duplicate(campaign, "Other", "", "https://linkedin.com/company/b", "")
        assert match.strength is MatchStrength.NONE

    def test_domain_beats_profile_when_they_point_at_different_companies(self):
        campaign = make_campaign()
        by_profile = make_company(
            campaign=campaign,
            name="P",
            website="https://p.example.com",
            profile_url="https://linkedin.com/company/acme",
        )
        by_domain = make_company(campaign=campaign, name="D", website="https://acme.com")
        match = find_duplicate(
            campaign, "Acme", "acme.com", "https://linkedin.com/company/acme", "SA"
        )
        assert match.matched_on == "domain"
        assert match.company == by_domain
        assert match.candidates == (by_domain, by_profile)

    def test_archived_company_is_a_strong_match(self):
        campaign = make_campaign()
        old = make_company(campaign=campaign, website="https://acme.com")
        old.archive()
        assert find_duplicate(campaign, "Acme", "acme.com").company == old

    def test_name_and_same_country_is_weak(self):
        campaign = make_campaign()
        existing = make_company(
            campaign=campaign, name="Acme Trading LLC", website="", country="SA"
        )
        match = find_duplicate(campaign, "ACME trading, L.L.C.", "", "", "sa")
        assert match.strength is MatchStrength.WEAK
        assert match.is_weak
        assert match.matched_on == "name_country"
        assert match.company == existing
        assert match.candidates == (existing,)

    def test_same_name_different_country_is_not_a_duplicate(self):
        campaign = make_campaign()
        make_company(campaign=campaign, name="Acme", website="", country="SA")
        assert find_duplicate(campaign, "Acme", "", "", "AE").strength is MatchStrength.NONE

    @pytest.mark.parametrize(("old_country", "new_country"), [("", "SA"), ("SA", ""), ("", "")])
    def test_unknown_country_does_not_rule_a_name_match_out(self, old_country, new_country):
        campaign = make_campaign()
        make_company(campaign=campaign, name="Acme", website="", country=old_country)
        match = find_duplicate(campaign, "Acme", "", "", new_country)
        assert match.strength is MatchStrength.WEAK

    def test_conflicting_domains_are_not_a_weak_match(self):
        campaign = make_campaign()
        make_company(campaign=campaign, name="Acme", website="https://acme.com", country="SA")
        match = find_duplicate(campaign, "Acme", "https://acme.io", "", "SA")
        assert match.strength is MatchStrength.NONE

    def test_conflicting_profiles_are_not_a_weak_match(self):
        campaign = make_campaign()
        make_company(
            campaign=campaign,
            name="Acme",
            website="",
            country="SA",
            profile_url="https://linkedin.com/company/acme-one",
        )
        match = find_duplicate(campaign, "Acme", "", "https://linkedin.com/company/acme-two", "SA")
        assert match.strength is MatchStrength.NONE

    def test_a_domain_on_only_one_side_is_still_weak(self):
        campaign = make_campaign()
        make_company(campaign=campaign, name="Acme", website="", country="SA")
        assert find_duplicate(campaign, "Acme", "acme.com", "", "SA").is_weak

    def test_archived_company_is_not_a_weak_candidate(self):
        campaign = make_campaign()
        make_company(campaign=campaign, name="Acme", website="").archive()
        assert find_duplicate(campaign, "Acme", "", "", "").strength is MatchStrength.NONE

    def test_weak_candidates_are_oldest_first_and_capped(self):
        campaign = make_campaign()
        made = []
        for i in range(MAX_WEAK_CANDIDATES + 3):
            company = make_company(campaign=campaign, name="Acme", website="", country="")
            Company.objects.filter(pk=company.pk).update(
                created_at=timezone.now() - timedelta(days=100 - i)
            )
            made.append(company)
        match = find_duplicate(campaign, "Acme", "", "", "")
        assert len(match.candidates) == MAX_WEAK_CANDIDATES
        assert list(match.candidates) == made[:MAX_WEAK_CANDIDATES]
        assert match.company == made[0]

    def test_exclude_leaves_a_company_out(self):
        campaign = make_campaign()
        company = make_company(campaign=campaign, name="Acme", website="https://acme.com")
        assert find_duplicate(campaign, "Acme", "acme.com", exclude=company) == NO_MATCH

    def test_same_domain_in_another_campaign_of_the_same_client_is_not_a_duplicate(self):
        client = make_client()
        one, two = make_campaign(client=client), make_campaign(client=client)
        make_company(
            campaign=one,
            name="Acme",
            website="https://acme.com",
            profile_url="https://linkedin.com/company/acme",
        )
        match = find_duplicate(two, "Acme", "acme.com", "https://linkedin.com/company/acme", "SA")
        assert match.strength is MatchStrength.NONE

    def test_same_domain_in_another_clients_campaign_is_not_a_duplicate(self):
        make_company(name="Acme", website="https://acme.com", country="SA")
        other = make_campaign()  # a campaign of another client
        match = find_duplicate(other, "Acme", "acme.com", "", "SA")
        assert match.strength is MatchStrength.NONE

    def test_query_count_is_small_and_does_not_grow_with_the_campaign(
        self, django_assert_max_num_queries
    ):
        campaign = make_campaign()
        for _ in range(30):
            make_company(campaign=campaign)
        make_company(campaign=campaign, name="Acme", website="", country="SA")
        with django_assert_max_num_queries(2):  # strong keys, then the name key
            assert find_duplicate(campaign, "Zed", "zed.example.org", "", "SA") == NO_MATCH
        with django_assert_max_num_queries(1):  # no website or profile: only the name query
            assert find_duplicate(campaign, "Acme", "", "", "SA").is_weak
        make_company(campaign=campaign, website="https://known.example.org")
        with django_assert_max_num_queries(1):  # a strong match stops after one query
            assert find_duplicate(campaign, "x", "known.example.org", "", "").is_strong


# ------------------------------------------------------------------ create_company: strong


class TestStrongMatchIsMergeSafe:
    def test_same_domain_twice_gives_one_company(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme", "https://acme.com")
        second = services.create_company(campaign, "Acme Again", "www.acme.com/about")
        assert first.created
        assert second.duplicate
        assert not second.created
        assert second.company == first.company
        assert second.match.strength is MatchStrength.STRONG
        assert second.match.matched_on == "domain"
        assert Company.objects.filter(campaign=campaign).count() == 1

    def test_existing_company_is_not_modified_by_a_duplicate_submission(self):
        campaign = make_campaign()
        first = services.create_company(
            campaign,
            "Acme",
            "https://acme.com",
            profile_url="https://linkedin.com/company/acme",
            country="SA",
        ).company
        before = _snapshot(first)
        again = services.create_company(
            campaign,
            "Acme International Holding",
            "http://www.acme.com/new-site",
            profile_url="https://linkedin.com/company/acme-intl",
            country="AE",
            input_source="manual",
        )
        assert again.duplicate
        assert _snapshot(again.company) == before
        assert Company.objects.filter(campaign=campaign).count() == 1

    def test_profile_url_strong_match_never_edits_the_existing_company(self):
        campaign = make_campaign()
        first = services.create_company(
            campaign,
            "Acme",
            "",
            profile_url="https://www.linkedin.com/company/acme",
            country="SA",
        ).company
        before = _snapshot(first)
        again = services.create_company(
            campaign,
            "Acme Corp",
            "https://acme-corp.com",
            profile_url="https://uk.linkedin.com/company/ACME/about/?utm_source=x",
            country="AE",
        )
        assert again.duplicate
        assert again.match.matched_on == "profile"
        assert again.company == first
        assert _snapshot(first) == before
        assert Company.objects.filter(campaign=campaign).count() == 1

    def test_archived_duplicate_is_restored_and_nothing_else_changes(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme", "acme.com", country="SA").company
        first.archive()
        before = _snapshot(first)
        again = services.create_company(campaign, "Renamed", "acme.com", country="AE")
        assert again.duplicate
        assert again.restored
        after = _snapshot(again.company)
        assert after.pop("archived_at") is None
        before.pop("archived_at")
        after.pop("updated_at")
        before.pop("updated_at")
        assert after == before

    def test_a_strong_match_is_never_flagged(self):
        campaign = make_campaign()
        services.create_company(campaign, "Acme", "acme.com")
        again = services.create_company(campaign, "Acme", "acme.com")
        assert not again.possible_duplicate
        assert again.company.possible_duplicate_of is None

    def test_idempotent_resubmission(self):
        campaign = make_campaign()
        results = [
            services.create_company(
                campaign, "Acme", "acme.com", profile_url="https://linkedin.com/company/acme"
            )
            for _ in range(3)
        ]
        assert [r.created for r in results] == [True, False, False]
        assert [r.duplicate for r in results] == [False, True, True]
        assert len({r.company.pk for r in results}) == 1
        assert Company.objects.count() == 1

    def test_same_domain_in_other_campaign_or_client_creates_a_separate_company(self):
        client = make_client()
        one, two = make_campaign(client=client), make_campaign(client=client)
        three = make_campaign()  # another client
        a = services.create_company(
            one, "Acme", "acme.com", profile_url="https://linkedin.com/company/a"
        )
        b = services.create_company(
            two, "Acme", "acme.com", profile_url="https://linkedin.com/company/a"
        )
        c = services.create_company(
            three, "Acme", "acme.com", profile_url="https://linkedin.com/company/a"
        )
        assert a.created
        assert b.created
        assert c.created
        assert {a.company.campaign_id, b.company.campaign_id, c.company.campaign_id} == {
            one.pk,
            two.pk,
            three.pk,
        }
        assert not b.possible_duplicate
        assert not c.possible_duplicate

    def test_race_on_unique_profile_returns_the_winner(self, monkeypatch):
        campaign = make_campaign()
        winner = make_company(
            campaign=campaign, website="", profile_url="https://linkedin.com/company/acme"
        )
        calls = iter([NO_MATCH])
        real = services.find_duplicate
        monkeypatch.setattr(
            services, "find_duplicate", lambda *a, **k: next(calls, None) or real(*a, **k)
        )
        result = services.create_company(
            campaign, "Acme", "", profile_url="https://www.linkedin.com/company/Acme/"
        )
        assert result.duplicate
        assert result.company == winner
        assert Company.objects.filter(campaign=campaign).count() == 1

    def test_integrity_error_without_a_strong_key_propagates(self, monkeypatch):
        def boom(self, *a, **k):
            raise IntegrityError("boom")

        monkeypatch.setattr(Company, "save", boom)
        with pytest.raises(IntegrityError):
            services.create_company(make_campaign(), "Acme")


# ------------------------------------------------------------------ create_company: weak


class TestWeakMatchFlagsButCreates:
    def test_same_name_and_country_is_created_and_flagged(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme Trading LLC", country="SA").company
        result = services.create_company(campaign, "acme trading", country="SA")
        assert result.created
        assert not result.duplicate
        assert result.possible_duplicate
        assert result.match.strength is MatchStrength.WEAK
        assert result.match.matched_on == "name_country"
        assert result.company != first
        assert result.company.possible_duplicate_of == first
        assert result.company.possible_duplicate is True
        assert result.similar == [first]
        assert result.warnings
        assert Company.objects.filter(campaign=campaign).count() == 2
        stored = Company.objects.get(pk=result.company.pk)
        assert stored.possible_duplicate_of_id == first.pk
        assert Company.objects.get(pk=first.pk).possible_duplicate_of is None

    def test_weak_match_does_not_touch_the_candidate(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme", "", country="SA").company
        before = _snapshot(first)
        services.create_company(campaign, "ACME", "acme.com", country="SA")
        assert _snapshot(first) == before

    def test_same_name_different_country_is_not_flagged(self):
        campaign = make_campaign()
        services.create_company(campaign, "Acme", "", country="SA")
        result = services.create_company(campaign, "Acme", "", country="AE")
        assert result.created
        assert not result.possible_duplicate
        assert result.company.possible_duplicate_of is None
        assert result.similar == []
        assert result.warnings == []

    def test_possible_duplicates_queryset(self):
        campaign = make_campaign()
        services.create_company(campaign, "Acme", "")
        flagged = services.create_company(campaign, "Acme", "").company
        services.create_company(campaign, "Other", "")
        assert list(Company.objects.filter(campaign=campaign).possible_duplicates()) == [flagged]

    def test_flag_points_at_the_oldest_candidate(self):
        campaign = make_campaign()
        a = services.create_company(campaign, "Acme", "", country="SA").company
        services.create_company(campaign, "Acme", "", country="SA")  # flagged against a
        c = services.create_company(campaign, "Acme", "", country="SA")
        assert c.company.possible_duplicate_of == a
        assert len(c.similar) == 2

    def test_weak_candidate_in_another_campaign_is_ignored(self):
        services.create_company(make_campaign(), "Acme", "")
        assert not services.create_company(make_campaign(), "Acme", "").possible_duplicate


class TestFlagIsWrittenOnce:
    def _flagged(self):
        campaign = make_campaign()
        first = services.create_company(campaign, "Acme", "").company
        flagged = services.create_company(campaign, "Acme", "").company
        return campaign, first, flagged

    def test_resaving_a_flagged_company_keeps_the_flag(self):
        _, first, flagged = self._flagged()
        fresh = Company.objects.get(pk=flagged.pk)
        fresh.status = "analyzing"
        fresh.save()
        assert Company.objects.get(pk=flagged.pk).possible_duplicate_of == first

    def test_changing_the_pointer_is_refused(self):
        campaign, first, flagged = self._flagged()
        other = make_company(campaign=campaign, name="Zed", website="")
        fresh = Company.objects.get(pk=flagged.pk)
        fresh.possible_duplicate_of = other
        with pytest.raises(ImmutableRecordError):
            fresh.save()
        assert Company.objects.get(pk=flagged.pk).possible_duplicate_of == first

    def test_clearing_the_pointer_is_refused(self):
        _, _, flagged = self._flagged()
        fresh = Company.objects.get(pk=flagged.pk)
        fresh.possible_duplicate_of = None
        with pytest.raises(ImmutableRecordError):
            fresh.save()

    def test_flagging_an_existing_unflagged_company_later_is_refused(self):
        campaign, first, _ = self._flagged()
        fresh = Company.objects.get(pk=first.pk)
        fresh.possible_duplicate_of = make_company(campaign=campaign, website="")
        with pytest.raises(ImmutableRecordError):
            fresh.save()

    def test_candidate_must_be_in_the_same_campaign(self):
        other = make_company()
        with pytest.raises(TenantMismatchError):
            make_company(campaign=make_campaign(), possible_duplicate_of=other)

    def test_a_company_cannot_be_its_own_duplicate(self):
        company = make_company()
        company.possible_duplicate_of = company
        with pytest.raises(ImmutableRecordError):
            company.save()
        fresh = Company(campaign=company.campaign, name="X", pk=company.pk)
        fresh.possible_duplicate_of_id = fresh.pk
        with pytest.raises(ValidationError):
            fresh.save()

    def test_candidate_is_protected_from_deletion(self):
        from django.db.models import ProtectedError

        _, first, _flagged = self._flagged()
        with pytest.raises(ProtectedError):
            first.delete()


# ------------------------------------------------------------------ imports record the match


def _batch(campaign, total=10):
    batch = make_import_batch(campaign=campaign, source="manual", total_count=total)
    return batch


class TestImportRowsRecordTheMatch:
    def test_created_row_has_no_match(self):
        batch = _batch(make_campaign())
        row = import_services.process_row(batch, 1, {"name": "Acme", "website": "acme.com"})
        assert row.outcome == ImportRowOutcome.CREATED
        assert (row.match_strength, row.matched_on, row.candidate) == ("", "", None)

    def test_same_domain_twice_is_one_company_and_one_duplicate_row(self):
        campaign = make_campaign()
        batch = _batch(campaign)
        first = import_services.process_row(batch, 1, {"name": "Acme", "website": "acme.com"})
        second = import_services.process_row(
            batch, 2, {"name": "ACME Inc", "website": "https://www.acme.com/x"}
        )
        batch.refresh_from_db()
        assert second.outcome == ImportRowOutcome.DUPLICATE
        assert second.match_strength == "strong"
        assert second.matched_on == "domain"
        assert second.candidate == first.company == second.company
        assert Company.objects.filter(campaign=campaign).count() == 1
        assert (batch.created_count, batch.duplicate_count) == (1, 1)

    def test_profile_duplicate_row(self):
        campaign = make_campaign()
        batch = _batch(campaign)
        first = import_services.process_row(
            batch, 1, {"name": "Acme", "profile_url": "https://www.linkedin.com/company/acme"}
        )
        second = import_services.process_row(
            batch, 2, {"name": "Acme 2", "profile_url": "https://uk.linkedin.com/company/Acme/"}
        )
        assert second.outcome == ImportRowOutcome.DUPLICATE
        assert second.matched_on == "profile"
        assert second.candidate == first.company

    def test_weak_match_row_is_created_and_points_at_the_candidate(self):
        campaign = make_campaign()
        batch = _batch(campaign)
        first = import_services.process_row(batch, 1, {"name": "Acme", "country": "SA"})
        second = import_services.process_row(batch, 2, {"name": "ACME LLC", "country": "sa"})
        assert second.outcome == ImportRowOutcome.CREATED
        assert second.match_strength == "weak"
        assert second.matched_on == "name_country"
        assert second.candidate == first.company
        assert second.company != first.company
        assert second.company is not None
        assert second.company.possible_duplicate_of == first.company

    def test_same_name_other_country_row_is_plain_created(self):
        batch = _batch(make_campaign())
        import_services.process_row(batch, 1, {"name": "Acme", "country": "SA"})
        second = import_services.process_row(batch, 2, {"name": "Acme", "country": "AE"})
        assert second.outcome == ImportRowOutcome.CREATED
        assert second.match_strength == ""

    def test_restored_row_is_a_strong_match(self):
        campaign = make_campaign()
        batch = _batch(campaign)
        first = import_services.process_row(batch, 1, {"name": "Acme", "website": "acme.com"})
        assert first.company is not None
        first.company.archive()
        second = import_services.process_row(batch, 2, {"name": "Acme", "website": "acme.com"})
        assert second.outcome == ImportRowOutcome.RESTORED
        assert second.match_strength == "strong"
        assert second.candidate == first.company

    def test_import_never_modifies_existing_company_fields(self):
        campaign = make_campaign()
        existing = services.create_company(
            campaign,
            "Acme",
            "https://acme.com",
            profile_url="https://linkedin.com/company/acme",
            country="SA",
        ).company
        before = _snapshot(existing)
        batch = _batch(campaign)
        rows = [
            {"name": "Changed", "website": "acme.com", "country": "AE"},
            {"name": "Changed2", "profile_url": "https://linkedin.com/company/acme/about"},
            {"name": "Acme", "country": "SA"},  # weak
            {
                "name": "Acme",
                "website": "https://acme.com/",
                "profile_url": "linkedin.com/company/x",
            },
        ]
        for number, raw in enumerate(rows, start=1):
            import_services.process_row(batch, number, raw)
        assert _snapshot(existing) == before

    def test_redelivered_row_is_not_matched_again(self):
        campaign = make_campaign()
        batch = _batch(campaign)
        import_services.process_row(batch, 1, {"name": "Acme", "website": "acme.com"})
        row = import_services.process_row(batch, 2, {"name": "Acme", "website": "acme.com"})
        again = import_services.process_row(batch, 2, {"name": "Acme", "website": "acme.com"})
        assert again.pk == row.pk
        assert ImportRow.objects.filter(batch=batch).count() == 2
        assert Company.objects.filter(campaign=campaign).count() == 1

    def test_failed_row_has_no_match(self):
        batch = _batch(make_campaign())
        row = import_services.process_row(batch, 1, {"name": ""})
        assert row.outcome == ImportRowOutcome.FAILED
        assert row.match_strength == ""
        assert row.candidate is None

    def test_match_columns_must_be_all_set_or_all_empty(self):
        batch = _batch(make_campaign())
        company = make_company(campaign=batch.campaign)
        with pytest.raises(IntegrityError), transaction.atomic():
            make_import_row(batch=batch, outcome="created", company=company, match_strength="weak")

    def test_candidate_must_be_in_the_batch_campaign(self):
        batch = _batch(make_campaign())
        own = make_company(campaign=batch.campaign)
        foreign = make_company()
        with pytest.raises(TenantMismatchError):
            make_import_row(
                batch=batch,
                outcome="created",
                company=own,
                match_strength="weak",
                matched_on="name_country",
                candidate=foreign,
            )

    def test_record_row_outcome_without_match_still_works(self):
        batch = _batch(make_campaign())
        company = make_company(campaign=batch.campaign)
        row = import_services.record_row_outcome(batch, 1, "created", company=company)
        assert row.match_strength == ""
        row2 = import_services.record_row_outcome(
            batch, 2, "created", company=company, match=NO_MATCH
        )
        assert row2.candidate is None

    def test_query_count_per_row_does_not_grow_with_the_campaign(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries_for_one_row(existing: int) -> int:
            campaign = make_campaign()
            for i in range(existing):
                make_company(
                    campaign=campaign, name=f"Existing {i}", website=f"https://e{i}.example.org"
                )
            batch = _batch(campaign, total=1)
            with CaptureQueriesContext(connection) as ctx:
                import_services.process_row(batch, 1, {"name": "New", "website": "new.example.org"})
            return len(ctx)

        assert queries_for_one_row(150) == queries_for_one_row(0)


# ------------------------------------------------------------------ migration backfill


def test_backfill_fills_keys_and_keeps_the_oldest_on_a_profile_clash():
    module = importlib.import_module("apps.companies.migrations.0003_company_duplicate_detection")
    from django.apps import apps

    campaign = make_campaign()
    old = make_company(
        campaign=campaign, name="The Acme Co.", profile_url="https://linkedin.com/company/acme"
    )
    other = make_company(campaign=campaign, name="Acme Two", profile_url="")
    Company.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
    clash = make_company(
        campaign=campaign, name="Clash", profile_url="https://linkedin.com/company/zzz"
    )
    # Simulate rows from before the columns existed: no keys, and a clashing profile identity.
    Company.objects.filter(pk=clash.pk).update(
        profile_url="https://www.linkedin.com/company/ACME/about"
    )
    Company.objects.filter(campaign=campaign).update(profile_key=None, name_key="")
    before = {c.pk: (c.updated_at, c.name, c.profile_url) for c in Company.objects.all()}

    module.backfill_keys(apps, None)

    rows = {c.pk: c for c in Company.objects.all()}
    assert rows[old.pk].profile_key == "linkedin:acme"
    assert rows[old.pk].name_key == "acme"
    assert rows[other.pk].profile_key is None
    assert rows[other.pk].name_key == "acme two"
    assert rows[clash.pk].profile_key is None  # the oldest row keeps the identity
    assert rows[clash.pk].profile_url == "https://www.linkedin.com/company/ACME/about"
    assert before == {pk: (c.updated_at, c.name, c.profile_url) for pk, c in rows.items()}
