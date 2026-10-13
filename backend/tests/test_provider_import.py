"""Provider import service (issue #67): preview annotation, import outcomes, idempotency, size
limits, the background job and safe error handling, all against the fake provider."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.companies import services as company_services
from apps.companies.models import Company, DataSource
from apps.core.models import AuditLog, Job, JobStatus
from apps.imports import provider_import as pi
from apps.imports.models import ImportBatch, ImportBatchStatus, ImportRow
from apps.imports.providers import (
    CompanySearchRequest,
    ProviderAuthError,
    ProviderCompany,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderRegistryError,
    ProviderUnavailable,
    default_registry,
)
from apps.imports.providers.dev import DevFakeProvider
from tests.factories import make_campaign, make_company, make_user

pytestmark = pytest.mark.django_db


def fake() -> DevFakeProvider:
    provider = default_registry.get("fake")
    assert isinstance(provider, DevFakeProvider)
    return provider


def pc(n: int, **overrides: Any) -> ProviderCompany:
    values: dict[str, Any] = {
        "name": f"Acme {n}",
        "website": f"https://acme{n}.example.com",
        "country": "SA",
        "industry": "Logistics",
        "employee_count": 50 + n,
        "headquarters": "Riyadh",
        "description": "A placeholder company.",
        "provider_id": f"p{n}",
        "provider_url": f"https://provider.example.com/r/{n}",
    }
    return ProviderCompany(**{**values, **overrides})


def ids_of(search_countries: list[str]) -> list[str]:
    page = fake().search(CompanySearchRequest(countries=tuple(search_countries), page_size=50))
    return [c.provider_id for c in page.items]


# ------------------------------------------------------------------ preview


def test_default_request_comes_from_campaign_rules() -> None:
    campaign = make_campaign(profile__countries=["SA"], profile__industries=["Fintech"])
    preview = pi.preview_provider_search(campaign, "fake")
    assert preview.request.countries == ("SA",)
    assert [i.company.name for i in preview.items] == ["Desert Pay"]
    assert fake().calls[-1].industries == ("Fintech",)


def test_query_string_and_overrides() -> None:
    campaign = make_campaign(profile__countries=["SA"])
    preview = pi.preview_provider_search(campaign, "fake", "pay")
    assert preview.request.query == "pay"
    req = pi.apply_filter_overrides(pi.default_search_request(campaign), {"countries": []})
    assert req.countries == ()
    with pytest.raises(Exception, match="bogus"):
        pi.apply_filter_overrides(req, {"bogus": 1})


def test_preview_annotates_without_writing() -> None:
    campaign = make_campaign(profile__countries=["SA"], profile__industries=["Software"])
    # Software in SA: Najd Tech (domain najd-tech.example.com)
    make_company(campaign=campaign, name="Other", website="https://www.najd-tech.example.com")
    before = (Company.objects.count(), DataSource.objects.count(), ImportBatch.objects.count())
    preview = pi.preview_provider_search(campaign, "fake")
    assert [i.status for i in preview.items] == [pi.DedupeStatus.DUPLICATE]
    assert preview.items[0].matched_company is not None
    assert preview.items[0].matched_on == "domain"
    assert preview.counts["duplicate"] == 1
    after = (Company.objects.count(), DataSource.objects.count(), ImportBatch.objects.count())
    assert after == before


def test_preview_statuses_new_weak_invalid_and_already_imported() -> None:
    campaign = make_campaign()
    make_company(campaign=campaign, name="Weak Name", website="", country="SA")
    items = pi.annotate_items(
        campaign,
        "fake",
        [
            pc(1),
            pc(2, name="Weak Name", website="https://other-weak.example.com"),
            pc(3, website="ftp://bad"),
        ],
    )
    assert [i.status for i in items] == [
        pi.DedupeStatus.NEW,
        pi.DedupeStatus.POSSIBLE_DUPLICATE,
        pi.DedupeStatus.INVALID,
    ]
    assert items[2].errors
    assert not items[2].importable
    pi.import_provider_companies(campaign, "fake", companies=[pc(1)])
    again = pi.annotate_items(campaign, "fake", [pc(1, website="https://changed.example.com")])
    assert again[0].status is pi.DedupeStatus.ALREADY_IMPORTED
    assert again[0].matched_on == "provider_key"


@pytest.mark.parametrize(
    "error",
    [ProviderRateLimited(12), ProviderAuthError(), ProviderUnavailable(), ProviderQuotaExceeded()],
)
def test_preview_raises_typed_provider_errors(error: Any) -> None:
    fake().fail_with = error
    with pytest.raises(type(error)):
        pi.preview_provider_search(make_campaign(), "fake")


def test_unknown_provider() -> None:
    with pytest.raises(ProviderRegistryError):
        pi.preview_provider_search(make_campaign(), "nope")


# ------------------------------------------------------------------ import


def test_import_inline_creates_companies_with_provenance() -> None:
    user = make_user()
    campaign = make_campaign()
    result = pi.import_provider_companies(campaign, "fake", companies=[pc(1), pc(2)], user=user)
    batch = result.batch
    assert not result.background
    assert batch.source == "provider"
    assert batch.status == ImportBatchStatus.COMPLETED
    assert (batch.total_count, batch.created_count) == (2, 2)
    assert batch.column_mapping["provider"] == "fake"
    assert batch.column_mapping["selection"] == "inline"
    assert batch.column_mapping["retrieved_at"]
    company = Company.objects.get(campaign=campaign, name="Acme 1")
    assert company.input_source == "provider"
    snapshot = company.research.get()
    assert snapshot.industry == "Logistics"
    assert snapshot.employee_count == 51
    source = snapshot.data_source
    assert (source.type, source.name, source.provider_reference) == ("provider", "fake", "p1")
    assert source.url == "https://provider.example.com/r/1"
    assert source.created_by == user
    actions = AuditLog.objects.filter(object_type="import_batch", object_id=batch.pk)
    assert sorted(actions.values_list("action", flat=True)) == ["create", "update"]


def test_import_by_ids_uses_the_search() -> None:
    campaign = make_campaign(profile__countries=["SA"])
    ids = ids_of(["SA"])[:3]
    search = CompanySearchRequest(countries=("SA",))
    result = pi.import_provider_companies(campaign, "fake", provider_ids=ids, search=search)
    assert result.batch.created_count == 3
    assert result.batch.column_mapping["selection"] == "ids"
    assert result.batch.column_mapping["search_request"]["countries"] == ["SA"]


def test_unknown_id_is_a_failed_row_and_batch_is_partial() -> None:
    campaign = make_campaign(profile__countries=["SA"])
    good = ids_of(["SA"])[0]
    result = pi.import_provider_companies(
        campaign,
        "fake",
        provider_ids=[good, "ghost"],
        search=CompanySearchRequest(countries=("SA",)),
    )
    batch = result.batch
    assert batch.status == ImportBatchStatus.PARTIAL
    failed = ImportRow.objects.get(batch=batch, outcome="failed")
    assert failed.error_code == pi.PROVIDER_COMPANY_NOT_FOUND


def test_reimport_is_a_duplicate_never_a_second_company() -> None:
    campaign = make_campaign()
    pi.import_provider_companies(campaign, "fake", companies=[pc(1)])
    # The provider id is the same but the website moved: the key still finds the company.
    again = pi.import_provider_companies(
        campaign, "fake", companies=[pc(1, website="https://moved.example.com")]
    )
    assert again.batch.duplicate_count == 1
    assert again.batch.created_count == 0
    assert Company.objects.filter(campaign=campaign).count() == 1
    row = ImportRow.objects.get(batch=again.batch)
    assert row.company is not None
    assert row.company.website == "https://acme1.example.com"  # never edited


def test_reimport_restores_an_archived_company() -> None:
    campaign = make_campaign()
    first = pi.import_provider_companies(campaign, "fake", companies=[pc(1)])
    company = ImportRow.objects.get(batch=first.batch).company
    assert company is not None
    company.archive()
    again = pi.import_provider_companies(
        campaign, "fake", companies=[pc(1, website="https://moved.example.com")]
    )
    assert again.batch.restored_count == 1
    company.refresh_from_db()
    assert not company.is_archived


def test_strong_and_weak_matches_follow_the_shared_pipeline() -> None:
    campaign = make_campaign()
    existing = make_company(campaign=campaign, name="Existing", website="https://dup.example.com")
    make_company(campaign=campaign, name="Same Name", website="", country="SA")
    result = pi.import_provider_companies(
        campaign,
        "fake",
        companies=[
            pc(1, name="Renamed", website="https://dup.example.com"),
            pc(2, name="Same Name", website="https://different.example.com"),
        ],
    )
    batch = result.batch
    assert (batch.duplicate_count, batch.created_count) == (1, 1)
    weak = ImportRow.objects.get(batch=batch, row_number=2)
    assert weak.match_strength == "weak"
    assert weak.company is not None
    assert weak.company.possible_duplicate_of is not None
    strong = ImportRow.objects.get(batch=batch, row_number=1)
    assert strong.company == existing
    existing.refresh_from_db()
    assert existing.name == "Existing"
    assert not existing.research.exists()  # duplicates get no data source


def test_invalid_company_fails_its_row_only() -> None:
    campaign = make_campaign()
    result = pi.import_provider_companies(
        campaign, "fake", companies=[pc(1), pc(2, website="ftp://bad")]
    )
    assert result.batch.status == ImportBatchStatus.PARTIAL
    assert (result.batch.created_count, result.batch.failed_count) == (1, 1)


def test_all_failed_batch_is_failed() -> None:
    result = pi.import_provider_companies(
        make_campaign(), "fake", companies=[pc(1, website="ftp://bad")]
    )
    assert result.batch.status == ImportBatchStatus.FAILED


def test_selection_validation(settings: Any) -> None:
    campaign = make_campaign()
    with pytest.raises(ValidationError):
        pi.import_provider_companies(campaign, "fake")
    with pytest.raises(ValidationError):
        pi.import_provider_companies(campaign, "fake", provider_ids=["a"], companies=[pc(1)])
    with pytest.raises(ValidationError):
        pi.import_provider_companies(campaign, "fake", provider_ids=["bad\x00id"])
    settings.PROVIDER_IMPORT_MAX_SELECTION = 3
    with pytest.raises(ValidationError, match="at most 3"):
        pi.import_provider_companies(campaign, "fake", companies=[pc(n) for n in range(4)])
    assert ImportBatch.objects.count() == 0  # refused before anything is written


def test_default_max_selection_is_100() -> None:
    assert pi.max_selection() == 100
    assert pi.inline_threshold() == 25
    campaign = make_campaign()
    with pytest.raises(ValidationError):
        pi.import_provider_companies(campaign, "fake", companies=[pc(n) for n in range(101)])


def test_archived_campaign_is_refused() -> None:
    from apps.campaigns import services

    campaign = make_campaign()
    services.archive_campaign(campaign)
    with pytest.raises(ValidationError):
        pi.import_provider_companies(campaign, "fake", companies=[pc(1)])


def test_duplicate_selection_entries_are_collapsed() -> None:
    result = pi.import_provider_companies(make_campaign(), "fake", companies=[pc(1), pc(1)])
    assert result.batch.total_count == 1


# ------------------------------------------------------------------ provider errors during import


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderRateLimited(30), "provider_rate_limited"),
        (ProviderUnavailable(), "provider_unavailable"),
        (ProviderQuotaExceeded(), "provider_quota_exceeded"),
        (ProviderAuthError(), "provider_auth"),
    ],
)
def test_provider_error_while_finding_ids_fails_the_batch_safely(error: Any, code: str) -> None:
    campaign = make_campaign()
    fake().fail_with = error
    result = pi.import_provider_companies(campaign, "fake", provider_ids=["fake-gulf-cargo"])
    batch = result.batch
    assert batch.status == ImportBatchStatus.FAILED
    assert batch.error_summary == str(error)
    row = ImportRow.objects.get(batch=batch)
    assert row.error_code == code
    assert result.provider_error is not None
    assert result.provider_error["code"] == code
    assert Company.objects.filter(campaign=campaign).count() == 0


def test_error_midway_leaves_earlier_rows_and_batch_partial() -> None:
    campaign = make_campaign()
    inline = pc(1)
    result = pi.import_provider_companies(
        campaign, "fake", companies=[inline]
    )  # baseline works without any provider call
    assert result.batch.status == ImportBatchStatus.COMPLETED
    assert fake().calls == []  # inline companies never call the provider


def test_upstream_text_never_leaks_into_rows_or_logs(caplog: Any) -> None:
    class Leaky(ProviderUnavailable):
        pass

    secret = "sk-live-abc123"  # pragma: allowlist secret
    try:
        raise RuntimeError(f"401 body token={secret}")
    except RuntimeError as exc:
        err = Leaky(provider_name="fake")
        err.__cause__ = exc
    fake().fail_with = err
    campaign = make_campaign()
    with caplog.at_level(logging.DEBUG):
        result = pi.import_provider_companies(campaign, "fake", provider_ids=["x"])
    stored = " ".join(
        [result.batch.error_summary, *ImportRow.objects.values_list("error_message", flat=True)]
    )
    assert secret not in stored
    assert secret not in caplog.text
    assert not any(r.exc_info for r in caplog.records)


# ------------------------------------------------------------------ background job


def test_large_selection_runs_in_a_job() -> None:
    user = make_user()
    campaign = make_campaign()
    companies = [pc(n) for n in range(30)]
    result = pi.import_provider_companies(campaign, "fake", companies=companies, user=user)
    assert result.background
    batch = result.batch
    batch.refresh_from_db()  # Q_CLUSTER is in sync mode under test: the task has run
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.created_count == 30
    assert batch.job is not None
    job = Job.objects.get(pk=batch.job.pk)
    assert job.type == "provider_import"
    assert job.status == JobStatus.SUCCEEDED
    assert (job.total_count, job.done_count) == (30, 30)
    assert Company.objects.filter(campaign=campaign, created_by_job_id=job.pk).count() == 30


def test_threshold_is_configurable(settings: Any) -> None:
    settings.PROVIDER_IMPORT_INLINE_MAX = 1
    result = pi.import_provider_companies(make_campaign(), "fake", companies=[pc(1), pc(2)])
    assert result.background


def test_job_task_is_idempotent_on_redelivery() -> None:
    campaign = make_campaign()
    result = pi.import_provider_companies(campaign, "fake", companies=[pc(n) for n in range(30)])
    batch = result.batch
    from apps.imports.provider_import import run_provider_import

    batch.refresh_from_db()
    assert batch.is_finished
    assert ImportRow.objects.filter(batch=batch).count() == 30
    assert callable(run_provider_import)
    assert Company.objects.filter(campaign=campaign).count() == 30


def test_company_service_untouched_by_preview() -> None:
    campaign = make_campaign()
    pi.preview_provider_search(campaign, "fake")
    assert company_services.find_duplicate(campaign, "Falcon Software").company is None
