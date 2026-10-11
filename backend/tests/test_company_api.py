"""Manual company entry, company list/detail, archive/restore and Analyze Company (issue #59)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns import services as campaign_services
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, Client
from apps.companies import analysis
from apps.companies.models import Company, CompanyResearch, CompanyStatus
from apps.companies.tasks import ANALYSIS_NOT_IMPLEMENTED
from apps.core.models import AuditAction, AuditLog, Job, JobItem, JobStatus
from apps.imports.models import ImportBatch, ImportBatchStatus, ImportRow
from tests.factories import make_campaign, make_client, make_company, make_research, make_user

pytestmark = [pytest.mark.api, pytest.mark.django_db]

ROLES = ["viewer", "reviewer", "manager", "admin", "global"]
CAN_EDIT = {"manager", "admin", "global"}
CAN_MANAGE = {"admin", "global"}


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def api_for(role: str, client: Client) -> APIClient:
    if role == "global":
        return as_user(make_user(is_superuser=True))
    user = make_user()
    grant_membership(client, user, role)
    return as_user(user)


def list_url(campaign: Campaign) -> str:
    return f"/api/v1/campaigns/{campaign.pk}/companies/"


def detail_url(company: Company, suffix: str = "") -> str:
    return f"/api/v1/companies/{company.pk}/{suffix}"


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"name": "Acme Logistics", "website": "acme-logistics.com"}
    data.update(overrides)
    return data


def details(response: Any) -> dict[str, Any]:
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    return dict(error["details"])


def codes(response: Any, field: str) -> list[str]:
    return [e["code"] for e in details(response)[field]]


@pytest.fixture
def world() -> tuple[Client, Campaign]:
    client = make_client()
    return client, make_campaign(client=client)


@pytest.fixture
def manager(world: tuple[Client, Campaign]) -> APIClient:
    return api_for("manager", world[0])


@pytest.fixture(autouse=True)
def queued_tasks(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Queue nothing by default: tests that want the placeholder task to run opt in."""
    queued: list[Any] = []

    def fake_async_task(*args: Any, **kwargs: Any) -> str:
        queued.append(args)
        return "task-1"

    monkeypatch.setattr(analysis, "async_task", fake_async_task)
    return queued


@pytest.fixture
def run_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q_CLUSTER is in sync mode in tests: run the real task inline."""
    from django_q.tasks import async_task

    monkeypatch.setattr(analysis, "async_task", async_task)


# ------------------------------------------------------------------ create: outcomes


def test_create_returns_201_with_company_batch_and_row(world, manager) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), body(country="sa"), format="json")
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["outcome"] == "created"
    assert (data["created"], data["duplicate"], data["restored"]) == (True, False, False)
    assert data["possible_duplicate"] is False
    assert data["possible_duplicate_of"] is None
    assert data["analysis"] is None
    company = data["company"]
    assert company["name"] == "Acme Logistics"
    assert company["domain"] == "acme-logistics.com"
    assert company["country"] == "SA"
    assert company["input_source"] == "manual"
    assert company["status"] == "pending"
    assert company["campaign"] == str(campaign.pk)
    assert company["latest_research"] is None
    assert company["archived_at"] is None
    batch = ImportBatch.objects.get(pk=data["batch_id"])
    assert (batch.source, batch.campaign_id) == ("manual", campaign.pk)
    assert (batch.status, batch.total_count, batch.created_count) == (
        ImportBatchStatus.COMPLETED,
        1,
        1,
    )
    row = ImportRow.objects.get(pk=data["import_row"]["id"])
    assert (row.batch_id, row.row_number, row.outcome) == (batch.pk, 1, "created")
    assert str(row.company_id) == company["id"]
    assert data["import_row"]["row_number"] == 1
    assert Company.objects.get(pk=company["id"]).created_by_id is not None


def test_create_is_audited(world, manager) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), body(), format="json")
    entry = AuditLog.objects.get(object_type="company", object_id=response.json()["company"]["id"])
    assert entry.action == AuditAction.CREATE
    assert entry.client_id == campaign.client_id
    assert entry.after is not None
    assert entry.after["name"] == "Acme Logistics"
    assert entry.after["import_batch"] == response.json()["batch_id"]


def test_create_with_profile_url_only(world, manager) -> None:
    _, campaign = world
    response = manager.post(
        list_url(campaign),
        {"name": "Acme", "profile_url": "https://www.linkedin.com/company/acme-inc"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["company"]["domain"] is None


def test_create_blank_optional_fields_are_fine(world, manager) -> None:
    _, campaign = world
    response = manager.post(
        list_url(campaign),
        body(profile_url="", country="", input=None),
        format="json",
    )
    assert response.status_code == 201, response.content


def test_strong_duplicate_returns_200_and_the_existing_company_untouched(world, manager) -> None:
    _, campaign = world
    first = manager.post(list_url(campaign), body(country="SA"), format="json").json()
    again = manager.post(
        list_url(campaign),
        body(
            name="ACME LOGISTICS LLC", website="https://www.ACME-logistics.com/about", country="AE"
        ),
        format="json",
    )
    assert again.status_code == 200, again.content
    data = again.json()
    assert (data["outcome"], data["duplicate"], data["created"]) == ("duplicate", True, False)
    assert data["company"]["id"] == first["company"]["id"]
    assert data["company"]["name"] == "Acme Logistics"  # nothing was overwritten
    assert data["company"]["country"] == "SA"
    assert data["import_row"]["match_strength"] == "strong"
    assert data["import_row"]["matched_on"] == "domain"
    assert Company.objects.filter(campaign=campaign).count() == 1
    batch = ImportBatch.objects.get(pk=data["batch_id"])
    assert (batch.duplicate_count, batch.created_count) == (1, 0)
    assert ImportBatch.objects.filter(campaign=campaign).count() == 2  # history of both attempts


def test_duplicate_by_profile_url(world, manager) -> None:
    _, campaign = world
    url = "https://www.linkedin.com/company/acme-inc"
    manager.post(list_url(campaign), {"name": "Acme", "profile_url": url}, format="json")
    again = manager.post(
        list_url(campaign),
        {"name": "Other name", "profile_url": url + "/?trk=abc"},
        format="json",
    )
    assert again.status_code == 200
    assert again.json()["import_row"]["matched_on"] == "profile"


def test_archived_duplicate_is_restored(world, manager) -> None:
    _, campaign = world
    company = make_company(campaign=campaign, name="Acme", website="acme.com")
    company.archive()
    response = manager.post(
        list_url(campaign), body(name="Acme", website="acme.com"), format="json"
    )
    assert response.status_code == 200
    data = response.json()
    assert (data["outcome"], data["duplicate"], data["restored"]) == ("restored", True, True)
    assert data["company"]["archived_at"] is None
    company.refresh_from_db()
    assert not company.is_archived
    entry = AuditLog.objects.get(object_type="company", object_id=company.pk)
    assert entry.action == AuditAction.RESTORE


def test_weak_match_creates_the_company_and_flags_it(world, manager) -> None:
    _, campaign = world
    first = manager.post(
        list_url(campaign),
        {
            "name": "Gulf Freight",
            "profile_url": "https://www.linkedin.com/company/gulf-freight",
            "country": "SA",
        },
        format="json",
    ).json()
    response = manager.post(
        list_url(campaign),
        body(name="Gulf Freight", website="gulf-freight.sa", country="SA"),
        format="json",
    )
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["possible_duplicate"] is True
    assert data["duplicate"] is False
    assert data["possible_duplicate_of"]["id"] == first["company"]["id"]
    assert data["company"]["possible_duplicate"] is True
    assert data["company"]["possible_duplicate_of"]["id"] == first["company"]["id"]
    assert data["import_row"]["match_strength"] == "weak"
    assert Company.objects.filter(campaign=campaign).count() == 2


def test_same_company_in_another_campaign_is_not_a_duplicate(world, manager) -> None:
    client, campaign = world
    other = make_campaign(client=client, name="Other campaign")
    manager.post(list_url(campaign), body(), format="json")
    response = manager.post(list_url(other), body(), format="json")
    assert response.status_code == 201


# ------------------------------------------------------------------ create: free-text input


@pytest.mark.parametrize(
    ("extra", "field", "expected"),
    [
        ({"input": "https://www.acme.com/about?utm_source=x"}, "website", "https://acme.com/about"),
        ({"input": "acme.com"}, "website", "acme.com"),
        (
            {"input": "https://www.linkedin.com/company/acme"},
            "profile_url",
            "https://www.linkedin.com/company/acme",
        ),
    ],
)
def test_input_is_routed_by_classify_input(world, manager, extra, field, expected) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), {"name": "Acme", **extra}, format="json")
    assert response.status_code == 201, response.content
    assert response.json()["company"][field] == expected


def test_input_can_supply_the_name(world, manager) -> None:
    _, campaign = world
    response = manager.post(
        list_url(campaign), {"input": "Acme Trading LLC", "website": "acme.com"}, format="json"
    )
    assert response.status_code == 201, response.content
    assert response.json()["company"]["name"] == "Acme Trading LLC"
    row = ImportRow.objects.get(pk=response.json()["import_row"]["id"])
    assert row.raw_data["input"] == "Acme Trading LLC"  # history keeps what was typed


def test_input_alone_still_needs_the_other_half(world, manager) -> None:
    _, campaign = world
    assert codes(
        manager.post(list_url(campaign), {"input": "acme.com"}, format="json"), "name"
    ) == ["name_required"]
    assert codes(manager.post(list_url(campaign), {"input": "Acme"}, format="json"), "website") == [
        "identifier_required"
    ]


def test_input_conflicting_with_a_given_field(world, manager) -> None:
    _, campaign = world
    response = manager.post(
        list_url(campaign), {"name": "Acme", "website": "a.com", "input": "b.com"}, format="json"
    )
    assert codes(response, "input") == ["input_conflicts_with_field"]


def test_input_with_a_dangerous_scheme_is_invalid(world, manager) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), body(input="javascript:alert(1)"), format="json")
    assert codes(response, "input") == ["input_invalid"]


# ------------------------------------------------------------------ create: validation


def test_name_is_required(world, manager) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), {"website": "acme.com"}, format="json")
    assert codes(response, "name") == ["name_required"]
    error = details(response)["name"][0]
    assert error["message"] == "Enter the company name."


def test_an_identifier_is_required(world, manager) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), {"name": "Acme"}, format="json")
    assert codes(response, "website") == ["identifier_required"]


def test_empty_body_reports_everything(world, manager) -> None:
    _, campaign = world
    found = details(manager.post(list_url(campaign), {}, format="json"))
    assert set(found) == {"name", "website"}


@pytest.mark.parametrize(
    ("payload", "field", "code"),
    [
        ({"website": "not a domain"}, "website", "website_invalid"),
        ({"website": "ftp://acme.com"}, "website", "website_invalid_scheme"),
        ({"website": "https://user:pw@acme.com"}, "website", "website_has_credentials"),
        ({"website": "a" * 2001}, "website", "website_too_long"),
        ({"profile_url": "linkedin.com/company/acme"}, "profile_url", "profile_url_invalid"),
        ({"profile_url": "javascript:alert(1)"}, "profile_url", "profile_url_invalid_scheme"),
        ({"country": "SAU"}, "country", "country_invalid_format"),
        ({"country": "ZZ"}, "country", "country_unknown"),
        ({"name": "x" * 301}, "name", "name_too_long"),
        ({"name": "bad\x07name"}, "name", "invalid_characters"),
    ],
)
def test_field_errors_carry_stable_codes(world, manager, payload, field, code) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), body(**payload), format="json")
    assert code in codes(response, field)


def test_unknown_and_non_text_fields_are_field_errors(world, manager) -> None:
    _, campaign = world
    found = details(
        manager.post(list_url(campaign), body(notes="x", name=123, analyze="maybe"), format="json")
    )
    assert {"notes", "name", "analyze"} <= set(found)


def test_invalid_input_creates_nothing(world, manager) -> None:
    _, campaign = world
    manager.post(list_url(campaign), {"name": "Acme"}, format="json")
    assert not Company.objects.filter(campaign=campaign).exists()
    assert not ImportBatch.objects.filter(campaign=campaign).exists()
    assert not AuditLog.objects.filter(object_type="company").exists()


def test_archived_campaign_takes_no_companies(world, manager) -> None:
    _, campaign = world
    campaign_services.archive_campaign(campaign)
    response = manager.post(list_url(campaign), body(), format="json")
    assert response.status_code == 400
    assert not Company.objects.exists()


def test_archived_client_takes_no_companies(world, manager) -> None:
    client, campaign = world
    campaign_services.archive_client(client)
    admin_api = api_for("global", client)
    assert admin_api.post(list_url(campaign), body(), format="json").status_code == 400
    assert not Company.objects.exists()


def test_non_json_object_body_is_rejected(world, manager) -> None:
    _, campaign = world
    assert manager.post(list_url(campaign), ["x"], format="json").status_code == 400


# ------------------------------------------------------------------ create: analyze flag


def test_analyze_flag_queues_a_job(world, manager, queued_tasks) -> None:
    _, campaign = world
    response = manager.post(list_url(campaign), body(analyze=True), format="json")
    assert response.status_code == 201, response.content
    job = response.json()["analysis"]
    assert job["status"] == "queued"
    assert job["type"] == "company_analysis"
    assert response.json()["company"]["analysis"]["id"] == job["id"]
    assert len(queued_tasks) == 1


def test_analyze_flag_on_a_duplicate_reuses_the_queued_job(world, manager, queued_tasks) -> None:
    _, campaign = world
    first = manager.post(list_url(campaign), body(analyze=True), format="json").json()
    again = manager.post(list_url(campaign), body(analyze=True), format="json")
    assert again.status_code == 200
    assert again.json()["analysis"]["id"] == first["analysis"]["id"]
    assert Job.objects.filter(type="company_analysis").count() == 1


def test_without_the_flag_no_job_is_made(world, manager) -> None:
    _, campaign = world
    manager.post(list_url(campaign), body(), format="json")
    assert not Job.objects.exists()


# ------------------------------------------------------------------ roles


@pytest.mark.parametrize("role", ROLES)
def test_create_needs_edit(world, role) -> None:
    client, campaign = world
    response = api_for(role, client).post(list_url(campaign), body(), format="json")
    assert response.status_code == (201 if role in CAN_EDIT else 403), (role, response.content)
    assert Company.objects.filter(campaign=campaign).exists() == (role in CAN_EDIT)


@pytest.mark.parametrize("role", ROLES)
def test_read_endpoints_are_open_to_every_role(world, role) -> None:
    client, campaign = world
    company = make_company(campaign=campaign)
    api = api_for(role, client)
    assert api.get(list_url(campaign)).status_code == 200
    assert api.get(detail_url(company)).status_code == 200


@pytest.mark.parametrize("role", ROLES)
def test_analyze_needs_edit(world, role) -> None:
    client, campaign = world
    company = make_company(campaign=campaign)
    response = api_for(role, client).post(detail_url(company, "analyze/"))
    assert response.status_code == (202 if role in CAN_EDIT else 403), (role, response.content)


@pytest.mark.parametrize("action", ["archive", "restore"])
@pytest.mark.parametrize("role", ROLES)
def test_archive_and_restore_need_manage(world, role, action) -> None:
    client, campaign = world
    company = make_company(campaign=campaign)
    if action == "restore":
        company.archive()
    response = api_for(role, client).post(detail_url(company, f"{action}/"))
    assert response.status_code == (200 if role in CAN_MANAGE else 403), (role, response.content)


def test_other_clients_campaign_and_company_are_404(world) -> None:
    _, campaign = world
    company = make_company(campaign=campaign)
    stranger = api_for("admin", make_client())
    for call in (
        lambda: stranger.get(list_url(campaign)),
        lambda: stranger.post(list_url(campaign), body(), format="json"),
        lambda: stranger.get(detail_url(company)),
        lambda: stranger.post(detail_url(company, "analyze/")),
        lambda: stranger.post(detail_url(company, "archive/")),
        lambda: stranger.post(detail_url(company, "restore/")),
    ):
        assert call().status_code == 404
    assert Company.objects.count() == 1
    assert not Job.objects.exists()


def test_unknown_campaign_and_company_are_404(manager) -> None:
    assert manager.get(f"/api/v1/campaigns/{uuid.uuid4()}/companies/").status_code == 404
    assert (
        manager.post(
            f"/api/v1/campaigns/{uuid.uuid4()}/companies/", body(), format="json"
        ).status_code
        == 404
    )
    assert manager.get(f"/api/v1/companies/{uuid.uuid4()}/").status_code == 404


def test_anonymous_gets_401(world) -> None:
    _, campaign = world
    company = make_company(campaign=campaign)
    api = APIClient()
    assert api.get(list_url(campaign)).status_code == 401
    assert api.post(list_url(campaign), body(), format="json").status_code == 401
    assert api.get(detail_url(company)).status_code == 401
    assert api.post(detail_url(company, "analyze/")).status_code == 401


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_companies_are_not_editable_or_deletable(world, method) -> None:
    client, campaign = world
    admin = api_for("admin", client)  # an unrouted write method fails closed (403) below MANAGE
    company = make_company(campaign=campaign)
    response = getattr(admin, method)(detail_url(company), {"name": "x"}, format="json")
    assert response.status_code == 405
    assert admin.delete(list_url(campaign)).status_code == 405


# ------------------------------------------------------------------ list


def test_list_is_paginated_and_hides_archived_by_default(world, manager) -> None:
    _, campaign = world
    live = [make_company(campaign=campaign, name=f"Live {n}") for n in range(3)]
    gone = make_company(campaign=campaign, name="Gone")
    gone.archive()
    make_company(campaign=make_campaign(client=world[0], name="Other"), name="Elsewhere")
    data = manager.get(list_url(campaign) + "?page_size=2").json()
    assert data["count"] == 3
    assert len(data["results"]) == 2
    assert data["next"]
    names = {r["name"] for r in manager.get(list_url(campaign)).json()["results"]}
    assert names == {c.name for c in live}
    assert [
        r["name"] for r in manager.get(list_url(campaign) + "?archived=true").json()["results"]
    ] == ["Gone"]
    assert manager.get(list_url(campaign) + "?archived=all").json()["count"] == 4


def test_list_filters(world, manager) -> None:
    _, campaign = world
    a = make_company(campaign=campaign, name="Alpha Freight", website="alpha-freight.com")
    b = make_company(campaign=campaign, name="Beta Retail", website="beta-retail.sa")
    c = make_company(campaign=campaign, name="Gamma", website="gamma.ae", input_source="csv")
    Company.objects.filter(pk=b.pk).update(status=CompanyStatus.ANALYZED)
    dup = make_company(
        campaign=campaign, name="Alpha Freight", website="alpha-freight.sa", possible_duplicate_of=a
    )

    def names(query: str) -> list[str]:
        response = manager.get(list_url(campaign) + query)
        assert response.status_code == 200, response.content
        return [r["id"] for r in response.json()["results"]]

    assert set(names("?search=freight")) == {str(a.pk), str(dup.pk)}
    assert names("?search=beta-retail.sa") == [str(b.pk)]  # domain and website
    assert names("?status=analyzed") == [str(b.pk)]
    assert names("?input_source=csv") == [str(c.pk)]
    assert names("?possible_duplicate=true") == [str(dup.pk)]
    assert str(dup.pk) not in names("?possible_duplicate=false")
    assert names("?ordering=-name&page_size=1") == [str(c.pk)]
    ordered = manager.get(list_url(campaign) + "?ordering=name").json()["results"]
    assert ordered[0]["name"] == "Alpha Freight"


@pytest.mark.parametrize(
    "query", ["?status=nope", "?archived=maybe", "?possible_duplicate=2", "?input_source=fax"]
)
def test_list_rejects_bad_filters(world, manager, query) -> None:
    _, campaign = world
    assert manager.get(list_url(campaign) + query).status_code == 400


def test_list_query_count_does_not_grow_with_the_page(world, manager) -> None:
    _, campaign = world
    anchor = make_company(campaign=campaign, name="Anchor", website="anchor.com")
    for n in range(2):
        make_company(campaign=campaign, name=f"Co {n}", possible_duplicate_of=anchor)
    manager.get(list_url(campaign))  # warm caches (content types, etc.)

    def queries() -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert manager.get(list_url(campaign)).status_code == 200
        return len(ctx)

    small = queries()
    for n in range(10):
        company = make_company(campaign=campaign, name=f"More {n}", possible_duplicate_of=anchor)
        job = Job.objects.create(type="company_analysis", client=campaign.client, campaign=campaign)
        JobItem.objects.create(
            job=job, client=campaign.client, subject_type="company", subject_id=company.pk
        )
    assert queries() == small


# ------------------------------------------------------------------ detail


def test_detail_has_latest_research_and_pointer(world, manager) -> None:
    _, campaign = world
    original = make_company(campaign=campaign, name="Original", website="original.com")
    company = make_company(
        campaign=campaign, name="Original", website="copy.com", possible_duplicate_of=original
    )
    make_research(company=company, industry="Old industry", researched_at="2020-01-01T00:00:00Z")
    newest = make_research(company=company, industry="Logistics")
    data = manager.get(detail_url(company)).json()
    assert data["latest_research"]["id"] == str(newest.pk)
    assert data["latest_research"]["industry"] == "Logistics"
    assert data["possible_duplicate"] is True
    assert data["possible_duplicate_of"]["id"] == str(original.pk)
    assert data["analysis"] is None


def test_detail_without_research_and_archived(world, manager) -> None:
    _, campaign = world
    company = make_company(campaign=campaign)
    company.archive()
    data = manager.get(detail_url(company)).json()
    assert data["latest_research"] is None
    assert data["archived_at"] is not None
    assert data["possible_duplicate_of"] is None


# ------------------------------------------------------------------ analyze


def test_analyze_queues_a_scoped_job_with_one_item(world, manager, queued_tasks) -> None:
    client, campaign = world
    company = make_company(campaign=campaign)
    response = manager.post(detail_url(company, "analyze/"))
    assert response.status_code == 202, response.content
    data = response.json()
    assert data["already_queued"] is False
    assert data["company_id"] == str(company.pk)
    job = Job.objects.get(pk=data["job"]["id"])
    assert (job.type, job.status, job.client_id, job.campaign_id) == (
        "company_analysis",
        JobStatus.QUEUED,
        client.pk,
        campaign.pk,
    )
    assert job.created_by is not None
    assert job.total_count == 1
    item = JobItem.objects.get(job=job)
    assert (item.subject_type, item.subject_id, item.client_id) == (
        "company",
        company.pk,
        client.pk,
    )
    assert job.queue_task_id == "task-1"
    assert len(queued_tasks) == 1


def test_analyze_is_idempotent_while_queued_or_running(world, manager, queued_tasks) -> None:
    _, campaign = world
    company = make_company(campaign=campaign)
    first = manager.post(detail_url(company, "analyze/")).json()
    second = manager.post(detail_url(company, "analyze/"))
    assert second.status_code == 202
    assert second.json()["already_queued"] is True
    assert second.json()["job"]["id"] == first["job"]["id"]
    Job.objects.filter(pk=first["job"]["id"]).update(status=JobStatus.RUNNING)
    assert manager.post(detail_url(company, "analyze/")).json()["job"]["id"] == first["job"]["id"]
    assert Job.objects.count() == 1
    assert len(queued_tasks) == 1


def test_analyze_again_after_the_job_finished_makes_a_new_job(world, manager) -> None:
    _, campaign = world
    company = make_company(campaign=campaign)
    first = manager.post(detail_url(company, "analyze/")).json()
    Job.objects.filter(pk=first["job"]["id"]).update(
        status=JobStatus.FAILED, finished_at="2030-01-01T00:00:00Z"
    )
    second = manager.post(detail_url(company, "analyze/")).json()
    assert second["already_queued"] is False
    assert second["job"]["id"] != first["job"]["id"]
    assert manager.get(detail_url(company)).json()["analysis"]["id"] == second["job"]["id"]


def test_analyze_two_companies_makes_two_jobs(world, manager) -> None:
    _, campaign = world
    a, b = make_company(campaign=campaign), make_company(campaign=campaign)
    assert manager.post(detail_url(a, "analyze/")).json()["already_queued"] is False
    assert manager.post(detail_url(b, "analyze/")).json()["already_queued"] is False
    assert Job.objects.count() == 2


def test_analyze_refuses_archived_company_and_campaign(world, manager) -> None:
    _, campaign = world
    archived = make_company(campaign=campaign)
    archived.archive()
    assert manager.post(detail_url(archived, "analyze/")).status_code == 400
    live = make_company(campaign=campaign)
    campaign_services.archive_campaign(campaign)
    assert manager.post(detail_url(live, "analyze/")).status_code == 400
    assert not Job.objects.exists()


def test_the_placeholder_task_fails_honestly_and_invents_nothing(world, manager, run_tasks) -> None:
    _, campaign = world
    company = make_company(campaign=campaign)
    response = manager.post(detail_url(company, "analyze/"))
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["status"] == "failed"
    assert ANALYSIS_NOT_IMPLEMENTED in job["error_summary"]
    assert (job["total_count"], job["done_count"], job["failed_count"]) == (1, 0, 1)
    item = JobItem.objects.get(job_id=job["id"])
    assert item.status == "failed"
    assert item.error == ANALYSIS_NOT_IMPLEMENTED
    assert not CompanyResearch.objects.exists()
    company.refresh_from_db()
    assert company.status == CompanyStatus.PENDING  # untouched


# ------------------------------------------------------------------ archive / restore


def test_archive_and_restore_are_idempotent_and_audited(world) -> None:
    client, campaign = world
    admin = api_for("admin", client)
    company = make_company(campaign=campaign)
    first = admin.post(detail_url(company, "archive/"))
    assert first.status_code == 200
    stamp = first.json()["archived_at"]
    assert stamp
    assert admin.post(detail_url(company, "archive/")).json()["archived_at"] == stamp
    assert admin.get(list_url(campaign)).json()["count"] == 0
    restored = admin.post(detail_url(company, "restore/"))
    assert restored.json()["archived_at"] is None
    assert admin.post(detail_url(company, "restore/")).status_code == 200
    actions = list(
        AuditLog.objects.filter(object_type="company", object_id=company.pk)
        .order_by("created_at")
        .values_list("action", flat=True)
    )
    assert actions == [AuditAction.ARCHIVE, AuditAction.RESTORE]  # no-ops write nothing


def test_archive_is_read_only_inside_an_archived_campaign(world) -> None:
    client, campaign = world
    admin = api_for("admin", client)
    company = make_company(campaign=campaign)
    campaign_services.archive_campaign(campaign)
    assert admin.post(detail_url(company, "archive/")).status_code == 400
    assert admin.post(detail_url(company, "restore/")).status_code == 400
