"""CSV bulk upload (issue #62): template, preview, import, cancel, the background task, resume,
limits, roles, tenant isolation, audit and file cleanup.

Q_CLUSTER is in sync mode under the test settings, so ``POST .../csv-import/`` runs the whole
import inline. Tests of the queued state replace ``csv_import.async_task`` with a stub (the
batch stays ``pending``, like before a worker picks it up) and run the task by hand.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns import services as campaign_services
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, Client
from apps.companies.models import Company
from apps.core.models import AuditAction, AuditLog, Job, JobStatus
from apps.imports import csv_import, uploads
from apps.imports import services as import_services
from apps.imports.csvparse import (
    TEMPLATE_COLUMNS,
    CsvFileError,
    CsvLimits,
    build_template_csv,
    iter_rows,
)
from apps.imports.models import ImportBatch, ImportBatchStatus, ImportRow, ImportRowOutcome
from apps.imports.tasks import process_csv_import
from tests.factories import make_campaign, make_client, make_company, make_user

pytestmark = [pytest.mark.api, pytest.mark.django_db]

ROLES = ["viewer", "reviewer", "manager", "admin", "global"]
EDIT_ROLES = {"manager", "admin", "global"}


# ------------------------------------------------------------------ helpers


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def user_with(role: str, client: Client) -> User:
    if role == "global":
        return make_user(is_superuser=True)
    user = make_user()
    grant_membership(client, user, role)
    return user


@pytest.fixture
def world() -> tuple[Client, Campaign]:
    client = make_client()
    return client, make_campaign(client=client)


@pytest.fixture
def manager(world: tuple[Client, Campaign]) -> User:
    return user_with("manager", world[0])


@pytest.fixture
def api(manager: User) -> APIClient:
    return as_user(manager)


@pytest.fixture
def campaign(world: tuple[Client, Campaign]) -> Campaign:
    return world[1]


def import_url(campaign: Campaign) -> str:
    return f"/api/v1/campaigns/{campaign.pk}/csv-import/"


def preview_url(campaign: Campaign) -> str:
    return f"/api/v1/campaigns/{campaign.pk}/csv-import/preview/"


def cancel_url(batch: ImportBatch | uuid.UUID | str) -> str:
    pk = batch.pk if isinstance(batch, ImportBatch) else batch
    return f"/api/v1/imports/{pk}/cancel/"


def make_csv(
    count: int = 3, *, start: int = 1, header: str = "company_name,website,country"
) -> bytes:
    lines = [header]
    for n in range(start, start + count):
        lines.append(f"Company {n},company{n}.example.com,SA")
    return ("\n".join(lines) + "\n").encode()


def upload(
    api: APIClient,
    campaign: Campaign,
    content: bytes,
    *,
    name: str = "companies.csv",
    mapping: dict[str, str] | str | None = None,
    preview: bool = False,
    content_type: str = "text/csv",
) -> Any:
    data: dict[str, Any] = {"file": SimpleUploadedFile(name, content, content_type=content_type)}
    if mapping is not None:
        data["mapping"] = mapping if isinstance(mapping, str) else json.dumps(mapping)
    url = preview_url(campaign) if preview else import_url(campaign)
    return api.post(url, data, format="multipart")


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Do not run the task inline: it stays queued, as before a worker picks it up."""
    calls: list[tuple[Any, ...]] = []

    def fake_async_task(func: str, *args: Any, **kwargs: Any) -> str:
        calls.append((func, args, kwargs))
        return f"task-{len(calls)}"

    monkeypatch.setattr(csv_import, "async_task", fake_async_task)
    return calls


def run_worker(job_id: Any, batch_id: Any) -> Any:
    """What the Django-Q2 worker does with the queued task."""
    return process_csv_import(str(job_id), batch_id=str(batch_id))


def stored_files() -> list[str]:
    return sorted(uploads.upload_storage().listdir("")[1])


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Any:
    storage = uploads.upload_storage()
    yield
    try:
        for name in storage.listdir("")[1]:
            storage.delete(name)
    except FileNotFoundError:
        pass


def accepted(response: Any) -> tuple[ImportBatch, Job]:
    assert response.status_code == 202, response.content
    body = response.json()
    batch = ImportBatch.objects.get(pk=body["batch"]["id"])
    return batch, Job.objects.get(pk=body["job_id"])


def companies(campaign: Campaign) -> list[Company]:
    return list(Company.objects.filter(campaign=campaign).order_by("name"))


# ------------------------------------------------------------------ template


def test_template_download_needs_a_login(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/imports/csv-template/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_template_download_is_the_parser_template(api: APIClient) -> None:
    response = api.get("/api/v1/imports/csv-template/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "companies-template.csv" in response["Content-Disposition"]
    assert response["Content-Disposition"].startswith("attachment")
    assert response.content == build_template_csv()
    assert response.content.startswith(b"\xef\xbb\xbf")
    header = response.content.decode("utf-8-sig").splitlines()[0]
    assert header.split(",") == list(TEMPLATE_COLUMNS)


def test_template_with_examples(api: APIClient) -> None:
    response = api.get("/api/v1/imports/csv-template/?examples=true")
    assert response.content == build_template_csv(include_examples=True)
    assert len(response.content.decode("utf-8-sig").splitlines()) == 4


def test_any_signed_in_user_may_download_the_template() -> None:
    assert as_user(make_user()).get("/api/v1/imports/csv-template/").status_code == 200


# ------------------------------------------------------------------ preview


def test_preview_shows_headers_mapping_rows_and_summary(api: APIClient, campaign: Campaign) -> None:
    content = make_csv(25, header="Company Name,Web Site,Country,Notes").replace(
        b"SA\n", b"SA,hello\n"
    )
    response = upload(api, campaign, content, preview=True)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["headers"] == ["Company Name", "Web Site", "Country", "Notes"]
    assert body["suggested_mapping"] == {
        "Company Name": "name",
        "Web Site": "website",
        "Country": "country",
    }
    assert body["mapping"] == body["suggested_mapping"]
    assert body["unmapped_headers"] == ["Notes"]
    assert body["company_fields"] == ["name", "website", "profile_url", "country"]
    assert len(body["rows"]) == 10
    first = body["rows"][0]
    assert first["row_number"] == 1
    assert first["raw"]["Notes"] == "hello"
    assert first["mapped"]["name"] == "Company 1"
    assert first["errors"] == []
    assert body["total_rows"] == 25
    assert body["error_rows"] == 0
    assert body["file"]["size_bytes"] == len(content)
    assert len(body["file"]["sha256"]) == 64
    assert body["file"]["encoding"] == "utf-8"
    assert body["file"]["delimiter"] == ","
    assert body["file"]["name"] == "companies.csv"
    assert body["warnings"] == []
    assert body["limits"]["max_rows"] == 5000
    assert body["active_import"] is None


def test_preview_stores_and_creates_nothing(api: APIClient, campaign: Campaign) -> None:
    assert upload(api, campaign, make_csv(5), preview=True).status_code == 200
    assert not ImportBatch.objects.exists()
    assert not Job.objects.exists()
    assert not Company.objects.exists()
    assert stored_files() == []
    assert not AuditLog.objects.filter(object_type="import_batch").exists()


def test_preview_applies_a_given_mapping(api: APIClient, campaign: Campaign) -> None:
    content = b"Org,URL,Place\nAcme,acme.example.com,Riyadh Co\n"
    chosen = {"Place": "name", "URL": "website"}
    body = upload(api, campaign, content, preview=True, mapping=chosen).json()
    assert body["mapping"] == chosen
    assert body["rows"][0]["mapped"] == {"name": "Riyadh Co", "website": "acme.example.com"}
    assert body["suggested_mapping"] != chosen


def test_preview_reports_what_the_import_would_record_per_row(
    api: APIClient, campaign: Campaign
) -> None:
    content = (
        b"company_name,website,country\n"
        b"Good,good.example.com,SA\n"
        b",x.example.com,SA\n"
        b"Bad,b.example.com,ZZ\n"
        b"Short,only\n"
    )
    body = upload(api, campaign, content, preview=True).json()
    rows = {r["row_number"]: r for r in body["rows"]}
    assert rows[1]["errors"] == []
    assert [e["code"] for e in rows[2]["errors"]] == ["name_required"]
    assert rows[2]["errors"][0]["field"] == "name"
    assert [e["code"] for e in rows[3]["errors"]] == ["country_unknown"]
    assert [e["code"] for e in rows[4]["errors"]] == ["column_count_mismatch"]
    assert body["error_rows"] == 1  # only the parser's own errors are counted for the file


def test_preview_warns_about_a_legacy_encoding(api: APIClient, campaign: Campaign) -> None:
    content = "company_name,website\nCaf\xe9 Ltd,cafe.example.com\n".encode("cp1252")
    body = upload(api, campaign, content, preview=True).json()
    assert body["file"]["encoding"] in {"windows-1252", "cp1252"}
    assert [w["code"] for w in body["warnings"]] == ["encoding_fallback"]
    assert body["rows"][0]["mapped"]["name"] == "Café Ltd"


def test_preview_flags_an_import_already_running(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    content = make_csv(2)
    batch, _ = accepted(upload(api, campaign, content))
    body = upload(api, campaign, content, preview=True).json()
    assert body["active_import"] == str(batch.pk)


# ------------------------------------------------------------------ whole-file rejections


@pytest.mark.parametrize("preview", [True, False])
@pytest.mark.parametrize(
    ("content", "code", "status"),
    [
        (b"", "empty_file", 400),
        (b"company_name,website\n", "no_data_rows", 400),
        (b"\x00\x01\x02\xff\xfe\x00binary\x00data" * 50, "binary_file", 400),
        (b"PK\x03\x04" + b"\x00" * 100, "unsupported_format", 400),
        (b"%PDF-1.7\n" + b"\x00" * 100, "unsupported_format", 400),
        (b"foo,bar\n1,2\n", "name_column_missing", 400),
    ],
)
def test_unusable_files_are_rejected_with_a_stable_code(
    api: APIClient, campaign: Campaign, content: bytes, code: str, status: int, preview: bool
) -> None:
    response = upload(api, campaign, content, preview=preview)
    assert response.status_code == status, response.content
    error = response.json()["error"]
    assert error["code"] == code
    assert error["details"]["issues"][0]["code"] == code
    assert error["request_id"] is not None
    assert not ImportBatch.objects.exists()
    assert stored_files() == []


def test_a_binary_file_is_rejected_whatever_its_content_type(
    api: APIClient, campaign: Campaign
) -> None:
    for content_type in ("text/csv", "application/octet-stream", "application/vnd.ms-excel"):
        response = upload(
            api,
            campaign,
            b"\x00\x01\x02\xff\xfe\x00binary\x00data" * 50,
            name="x.csv",
            content_type=content_type,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "binary_file"


def test_a_csv_is_accepted_whatever_its_content_type_or_extension(
    api: APIClient, campaign: Campaign
) -> None:
    response = upload(
        api, campaign, make_csv(2), name="export.txt", content_type="application/octet-stream"
    )
    batch, _ = accepted(response)
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.original_filename == "export.txt"


def test_more_than_5000_rows_is_rejected_before_anything_is_queued(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    response = upload(api, campaign, make_csv(5001))
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "too_many_rows"
    assert not ImportBatch.objects.exists()
    assert not Job.objects.exists()
    assert queued == []
    assert stored_files() == []


def test_exactly_5000_rows_is_accepted(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(5000)))
    assert batch.total_count == 5000
    assert job.total_count == 5000
    assert batch.status == ImportBatchStatus.PENDING


def test_an_oversized_file_is_413_with_a_code(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(csv_import, "csv_limits", lambda: CsvLimits(max_file_bytes=1000))
    for preview in (True, False):
        response = upload(api, campaign, make_csv(200), preview=preview)
        assert response.status_code == 413, response.content
        assert response.json()["error"]["code"] == "file_too_large"
    assert not ImportBatch.objects.exists()


def test_a_huge_request_is_refused_from_its_headers_before_reading_the_body(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(csv_import, "csv_limits", lambda: CsvLimits(max_file_bytes=1000))
    seen: list[str] = []
    monkeypatch.setattr(csv_import, "submit_csv_import", lambda *a, **k: seen.append("called"))
    response = api.post(
        import_url(campaign),
        data=b"x",
        content_type="multipart/form-data; boundary=x",
        CONTENT_LENGTH=str(10 * 1024 * 1024),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert seen == []


def test_the_file_field_is_required(api: APIClient, campaign: Campaign) -> None:
    response = api.post(import_url(campaign), {"mapping": "{}"}, format="multipart")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "file" in response.json()["error"]["details"]


def test_a_json_body_is_not_accepted(api: APIClient, campaign: Campaign) -> None:
    response = api.post(import_url(campaign), {"file": "x"}, format="json")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


@pytest.mark.parametrize(
    ("mapping", "code"),
    [
        ({"Nope": "name"}, "unknown_column"),
        ({"company_name": "colour"}, "unknown_field"),
        ({"website": "website"}, "name_column_missing"),
        ({"company_name": "name", "website": "name"}, "duplicate_target"),
    ],
)
def test_a_bad_mapping_is_rejected_with_every_problem(
    api: APIClient, campaign: Campaign, mapping: dict[str, str], code: str
) -> None:
    response = upload(api, campaign, make_csv(2), mapping=mapping)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] in {code, "unknown_column", "name_column_missing"}
    assert code in {i["code"] for i in error["details"]["issues"]}
    assert not ImportBatch.objects.exists()


@pytest.mark.parametrize("mapping", ["not json", "[1]", '{"a": 1}', '"x"'])
def test_a_mapping_that_is_not_a_string_to_string_object_is_a_validation_error(
    api: APIClient, campaign: Campaign, mapping: str
) -> None:
    response = upload(api, campaign, make_csv(2), mapping=mapping)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "mapping" in response.json()["error"]["details"]


# ------------------------------------------------------------------ the import, happy path


def test_import_of_the_sample_template_runs_to_completion(
    api: APIClient, campaign: Campaign, manager: User
) -> None:
    content = build_template_csv(include_examples=True)
    response = upload(api, campaign, content, name="companies-template.csv")
    assert response.status_code == 202, response.content
    body = response.json()
    batch, job = accepted(response)
    assert body["status_url"] == f"/api/v1/imports/{batch.pk}/"
    assert response["Location"] == body["status_url"]
    assert body["batch"]["id"] == str(batch.pk)
    assert body["job_id"] == str(job.pk)

    assert batch.source == "csv"
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.original_filename == "companies-template.csv"
    assert batch.file_size == len(content)
    assert len(batch.file_sha256) == 64
    assert batch.column_mapping == {
        "company_name": "name",
        "website": "website",
        "profile_url": "profile_url",
        "country": "country",
    }
    assert (batch.total_count, batch.created_count, batch.failed_count) == (3, 3, 0)
    assert batch.created_by == manager
    assert batch.job_id == job.pk
    assert batch.started_at is not None
    assert batch.finished_at is not None

    assert job.status == JobStatus.SUCCEEDED
    assert (job.total_count, job.done_count, job.failed_count) == (3, 3, 0)
    assert job.type == "process_csv_import"
    assert job.client_id == campaign.client_id
    assert job.queue_task_id

    names = [c.name for c in companies(campaign)]
    assert names == ["Example Logistics", "Example Trading Co", "شركة المثال للتجارة"]
    assert {c.input_source for c in companies(campaign)} == {"csv"}
    assert {c.created_by_job_id for c in companies(campaign)} == {job.pk}
    rows = list(ImportRow.objects.filter(batch=batch).order_by("row_number"))
    assert [r.outcome for r in rows] == ["created"] * 3
    assert rows[0].raw_data["industry"] == "Retail"  # unmapped columns are kept as submitted
    assert rows[1].country == "AE"  # country names are normalized by the parser
    assert stored_files() == []  # the upload is deleted when the batch ends


def test_the_response_batch_has_the_documented_shape(api: APIClient, campaign: Campaign) -> None:
    body = upload(api, campaign, make_csv(2)).json()
    batch = body["batch"]
    assert set(batch) == {
        "id",
        "campaign",
        "client",
        "source",
        "status",
        "original_filename",
        "file_size",
        "file_sha256",
        "column_mapping",
        "total_count",
        "created_count",
        "duplicate_count",
        "restored_count",
        "skipped_count",
        "failed_count",
        "processed_count",
        "progress_percent",
        "error_summary",
        "job",
        "created_by",
        "created_at",
        "started_at",
        "finished_at",
    }
    assert batch["campaign"] == str(campaign.pk)
    assert batch["job"] == body["job_id"]
    assert batch["progress_percent"] == 100


def test_import_returns_before_any_row_is_processed_when_queued(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    response = upload(api, campaign, make_csv(500))
    batch, job = accepted(response)
    assert batch.status == ImportBatchStatus.PENDING
    assert response.json()["batch"]["progress_percent"] == 0
    assert batch.total_count == 500
    assert job.status == JobStatus.QUEUED
    assert job.total_count == 500
    assert job.queue_task_id == "task-1"
    assert Company.objects.count() == 0
    assert queued == [
        (
            "apps.imports.tasks.process_csv_import",
            (str(job.pk),),
            {"batch_id": str(batch.pk)},
        )
    ]
    assert stored_files() == [f"{batch.pk}.csv"]  # waits for the worker


def test_integration_500_rows_in_the_background(
    api: APIClient, campaign: Campaign, queued: list[Any], settings: Any
) -> None:
    settings.IMPORT_CHUNK_SIZE = 100
    batch, job = accepted(upload(api, campaign, make_csv(500)))
    result = run_worker(job.pk, batch.pk)

    batch.refresh_from_db()
    job.refresh_from_db()
    assert result["status"] == "completed"
    assert batch.status == ImportBatchStatus.COMPLETED
    assert (batch.total_count, batch.created_count) == (500, 500)
    assert Company.objects.filter(campaign=campaign).count() == 500
    assert ImportRow.objects.filter(batch=batch).count() == 500
    assert job.status == JobStatus.SUCCEEDED
    assert (job.done_count, job.failed_count) == (500, 0)
    assert stored_files() == []


def test_progress_is_saved_in_chunks(
    api: APIClient, campaign: Campaign, queued: list[Any], settings: Any, monkeypatch: Any
) -> None:
    settings.IMPORT_CHUNK_SIZE = 100
    batch, job = accepted(upload(api, campaign, make_csv(250)))
    seen: list[tuple[int, int]] = []
    real = csv_import._sync_job

    def spy(job_: Job, batch_: ImportBatch, **kwargs: Any) -> None:
        real(job_, batch_, **kwargs)
        seen.append((job_.done_count + job_.failed_count, batch_.processed_count))

    monkeypatch.setattr(csv_import, "_sync_job", spy)
    run_worker(job.pk, batch.pk)
    processed = [done for done, _ in seen]
    assert 100 in processed  # a save every 100 rows
    assert 200 in processed
    assert processed == sorted(processed)
    assert all(job_done == batch_done for job_done, batch_done in seen)
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED


# ------------------------------------------------------------------ row outcomes


def test_duplicates_inside_the_file_and_against_existing_companies(
    api: APIClient, campaign: Campaign
) -> None:
    existing = make_company(campaign=campaign, name="Already Here", website="here.example.com")
    content = (
        b"company_name,website,country\n"
        b"Fresh One,fresh.example.com,SA\n"
        b"Fresh One Again,https://www.fresh.example.com/about,SA\n"  # same domain as row 1
        b"Already Here Ltd,here.example.com,SA\n"  # existing company
        b"Other,other.example.com,AE\n"
    )
    batch, job = accepted(upload(api, campaign, content))
    assert batch.status == ImportBatchStatus.COMPLETED
    assert (batch.created_count, batch.duplicate_count, batch.failed_count) == (2, 2, 0)
    rows = {r.row_number: r for r in ImportRow.objects.filter(batch=batch)}
    assert rows[1].outcome == ImportRowOutcome.CREATED
    assert rows[2].outcome == ImportRowOutcome.DUPLICATE
    assert rows[2].company_id == rows[1].company_id
    assert rows[3].outcome == ImportRowOutcome.DUPLICATE
    assert rows[3].company_id == existing.pk
    assert rows[3].match_strength == "strong"
    assert Company.objects.filter(campaign=campaign).count() == 3  # existing + 2 new
    assert (job.done_count, job.failed_count) == (4, 0)


def test_an_archived_duplicate_is_restored(api: APIClient, campaign: Campaign) -> None:
    old = make_company(campaign=campaign, name="Comeback", website="comeback.example.com")
    old.archive()
    content = b"company_name,website\nComeback,comeback.example.com\n"
    batch, _ = accepted(upload(api, campaign, content))
    assert (batch.restored_count, batch.created_count) == (1, 0)
    old.refresh_from_db()
    assert old.archived_at is None


def test_bad_rows_are_recorded_with_reasons_and_do_not_stop_the_batch(
    api: APIClient, campaign: Campaign
) -> None:
    content = (
        b"company_name,website,profile_url,country\n"
        b"Good One,good.example.com,,SA\n"
        b",noname.example.com,,SA\n"  # row 2: no name
        b"Bad Site,not a domain!,,SA\n"  # row 3: website
        b"Bad Country,bc.example.com,,ZZ\n"  # row 4
        b"Short Row,only.example.com\n"  # row 5: column count
        b"\n"  # blank line: skipped, still takes a number
        b"Good Two,good2.example.com,https://www.linkedin.com/company/good-two,AE\n"  # row 7
    )
    batch, job = accepted(upload(api, campaign, content))
    assert batch.status == ImportBatchStatus.PARTIAL
    assert (batch.total_count, batch.created_count, batch.failed_count) == (6, 2, 4)
    assert "4 of 6 row(s) failed" in batch.error_summary
    rows = {r.row_number: r for r in ImportRow.objects.filter(batch=batch)}
    assert sorted(rows) == [1, 2, 3, 4, 5, 7]
    assert rows[2].error_code == "name_required"
    assert rows[3].error_code == "website_invalid"
    assert rows[4].error_code == "country_unknown"
    assert rows[5].error_code == "column_count_mismatch"
    assert all(rows[n].outcome == "failed" and rows[n].error_message for n in (2, 3, 4, 5))
    assert rows[7].outcome == "created"
    assert [c.name for c in companies(campaign)] == ["Good One", "Good Two"]
    assert job.status == JobStatus.PARTIAL
    assert (job.done_count, job.failed_count) == (2, 4)


def test_a_file_where_every_row_fails_ends_failed(api: APIClient, campaign: Campaign) -> None:
    content = b"company_name,website\n,a.example.com\n,b.example.com\n"
    batch, job = accepted(upload(api, campaign, content))
    assert batch.status == ImportBatchStatus.FAILED
    assert batch.failed_count == 2
    assert job.status == JobStatus.FAILED
    assert Company.objects.count() == 0


def test_formulas_are_stored_as_typed_and_never_evaluated(
    api: APIClient, campaign: Campaign
) -> None:
    payload = "=cmd|' /C calc'!A0"
    content = f'company_name,website\n"{payload}",evil.example.com\n'.encode()
    batch, _ = accepted(upload(api, campaign, content))
    assert batch.created_count == 1
    assert Company.objects.get(campaign=campaign).name == payload
    assert ImportRow.objects.get(batch=batch).raw_data["company_name"] == payload


def test_a_credential_like_column_is_scrubbed_from_the_stored_row(
    api: APIClient, campaign: Campaign
) -> None:
    content = b"company_name,website,api_key\nAcme,acme.example.com,YOUR_API_KEY_VALUE\n"
    batch, _ = accepted(upload(api, campaign, content))
    row = ImportRow.objects.get(batch=batch)
    assert row.raw_data["api_key"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("encoding", "header", "delimiter"),
    [
        ("utf-8-sig", "company_name,website", ","),
        ("utf-16", "company_name,website", ","),
        ("utf-8", "company_name;website", ";"),
        ("utf-8", "company_name\twebsite", "\t"),
        ("cp1256", "company_name,website", ","),
    ],
)
def test_encodings_and_delimiters(
    api: APIClient, campaign: Campaign, encoding: str, header: str, delimiter: str
) -> None:
    name = "شركة النور" if encoding == "cp1256" else "Nour Trading"
    text = f"{header}\n{name}{delimiter}nour.example.com\n"
    batch, _ = accepted(upload(api, campaign, text.encode(encoding)))
    assert batch.status == ImportBatchStatus.COMPLETED, batch.error_summary
    assert Company.objects.get(campaign=campaign).name == name


def test_mapping_override(api: APIClient, campaign: Campaign) -> None:
    content = b"Org,Homepage,Place\nAcme Co,acme.example.com,SA\nGlobex,globex.example.com,AE\n"
    mapping = {"Org": "name", "Homepage": "website", "Place": "country"}
    batch, _ = accepted(upload(api, campaign, content, mapping=mapping))
    assert batch.column_mapping == mapping
    assert batch.created_count == 2
    acme = Company.objects.get(campaign=campaign, name="Acme Co")
    assert acme.country == "SA"
    assert acme.website == "acme.example.com"


def test_a_column_the_user_left_unmapped_never_feeds_a_company_field(
    api: APIClient, campaign: Campaign
) -> None:
    # "website" is a real column but is not in the mapping the user chose: it must be ignored.
    content = b"Org,website\nAcme,acme.example.com\n"
    batch, _ = accepted(upload(api, campaign, content, mapping={"Org": "name"}))
    company = Company.objects.get(campaign=campaign)
    assert company.website == ""
    assert ImportRow.objects.get(batch=batch).raw_data["website"] == ""


# ------------------------------------------------------------------ duplicate upload guard


def test_the_same_file_while_a_batch_is_active_is_409_with_the_existing_batch(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    content = make_csv(3)
    first, _ = accepted(upload(api, campaign, content))
    response = upload(api, campaign, content, name="renamed.csv")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "import_in_progress"
    assert error["details"]["batch"]["id"] == str(first.pk)
    assert error["details"]["status_url"] == f"/api/v1/imports/{first.pk}/"
    assert ImportBatch.objects.count() == 1
    assert len(queued) == 1
    assert stored_files() == [f"{first.pk}.csv"]  # the refused upload left nothing behind

    first.refresh_from_db()
    assert first.status == ImportBatchStatus.PENDING  # and did not disturb the first one


def test_the_guard_also_covers_a_processing_batch(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    from apps.imports.services import start_batch

    content = make_csv(3)
    first, _ = accepted(upload(api, campaign, content))
    start_batch(first)
    assert upload(api, campaign, content).status_code == 409


def test_a_changed_file_is_a_different_file(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    accepted(upload(api, campaign, make_csv(3)))
    accepted(upload(api, campaign, make_csv(3) + b"Extra,extra.example.com,SA\n"))
    assert ImportBatch.objects.count() == 2


def test_the_same_file_into_another_campaign_is_fine(
    api: APIClient, world: tuple[Client, Campaign], queued: list[Any]
) -> None:
    other = make_campaign(client=world[0], name="Second")
    accepted(upload(api, world[1], make_csv(3)))
    accepted(upload(api, other, make_csv(3)))
    assert ImportBatch.objects.count() == 2


def test_the_same_file_may_be_imported_again_once_the_first_batch_ended(
    api: APIClient, campaign: Campaign
) -> None:
    content = make_csv(3)
    first, _ = accepted(upload(api, campaign, content))
    assert first.status == ImportBatchStatus.COMPLETED
    second, _ = accepted(upload(api, campaign, content))
    assert second.pk != first.pk
    assert (second.created_count, second.duplicate_count) == (0, 3)  # rows come back as duplicates
    assert Company.objects.filter(campaign=campaign).count() == 3


def test_a_cancelled_batch_frees_the_file(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    content = make_csv(3)
    first, _ = accepted(upload(api, campaign, content))
    assert api.post(cancel_url(first)).status_code == 200
    accepted(upload(api, campaign, content))


# ------------------------------------------------------------------ cancel


def test_cancel_a_pending_batch(
    api: APIClient, campaign: Campaign, queued: list[Any], manager: User
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(5)))
    response = api.post(cancel_url(batch))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["finished_at"] is not None
    assert body["error_summary"] == "Cancelled by the user."
    assert stored_files() == []  # the upload is deleted at once
    entry = AuditLog.objects.filter(object_type="import_batch", object_id=batch.pk).latest(
        "created_at"
    )
    assert entry.action == AuditAction.UPDATE
    assert entry.actor_id == manager.pk
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["status"] == "pending"
    assert entry.after["status"] == "cancelled"

    # The worker later picks the task up: it finds a cancelled batch and does nothing.
    run_worker(job.pk, batch.pk)
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED  # nothing failed; zero items were processed
    assert Company.objects.count() == 0
    assert ImportRow.objects.count() == 0


def test_cancelling_twice_is_fine_and_cancelling_a_finished_batch_is_409(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    batch, _ = accepted(upload(api, campaign, make_csv(2)))
    assert api.post(cancel_url(batch)).status_code == 200
    assert api.post(cancel_url(batch)).status_code == 200
    assert AuditLog.objects.filter(object_type="import_batch", action="update").count() == 1

    done, _ = accepted(upload(api, campaign, make_csv(2, start=10)))
    assert done.status == ImportBatchStatus.PENDING
    run_worker(Job.objects.get(import_batches=done).pk, done.pk)
    response = api.post(cancel_url(done))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "batch_already_finished"


def test_cancel_stops_a_running_import_at_the_next_chunk(
    api: APIClient, campaign: Campaign, queued: list[Any], settings: Any, monkeypatch: Any
) -> None:
    settings.IMPORT_CHUNK_SIZE = 5
    batch, job = accepted(upload(api, campaign, make_csv(40)))
    real = import_services.process_row

    def process(batch_: ImportBatch, number: int, raw: Any, **kwargs: Any) -> Any:
        if number == 7:  # the user cancels while the worker is mid-run
            api.post(cancel_url(batch_))
        return real(batch_, number, raw, **kwargs)

    monkeypatch.setattr(csv_import, "process_row", process)
    run_worker(job.pk, batch.pk)

    batch.refresh_from_db()
    job.refresh_from_db()
    assert batch.status == ImportBatchStatus.CANCELLED
    assert 0 < batch.processed_count < 40
    assert batch.processed_count <= 7  # the batch was closed: no row after it was recorded
    assert Company.objects.filter(campaign=campaign).count() == batch.created_count
    assert job.status in {JobStatus.SUCCEEDED, JobStatus.PARTIAL}
    assert job.done_count + job.failed_count == batch.processed_count
    assert stored_files() == []


# ------------------------------------------------------------------ resume and idempotency


def test_a_worker_restart_resumes_without_duplicates(
    api: APIClient, campaign: Campaign, queued: list[Any], manager: User
) -> None:
    from apps.imports.services import process_row, start_batch

    batch, job = accepted(upload(api, campaign, make_csv(10)))
    # The first worker got through rows 1-4 and died before the task finished.
    start_batch(batch)
    for number in range(1, 5):
        process_row(
            batch,
            number,
            {
                "name": f"Company {number}",
                "website": f"company{number}.example.com",
                "country": "SA",
            },
            user=manager,
        )
    assert Company.objects.count() == 4

    run_worker(job.pk, batch.pk)
    batch.refresh_from_db()
    job.refresh_from_db()
    assert batch.status == ImportBatchStatus.COMPLETED
    assert (batch.total_count, batch.created_count) == (10, 10)
    assert Company.objects.count() == 10
    assert ImportRow.objects.filter(batch=batch).count() == 10
    assert (job.status, job.done_count) == (JobStatus.SUCCEEDED, 10)
    assert stored_files() == []


def test_running_the_task_again_changes_nothing(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(6)))
    run_worker(job.pk, batch.pk)
    before = (Company.objects.count(), ImportRow.objects.count(), AuditLog.objects.count())
    assert run_worker(job.pk, batch.pk) is None  # the wrapper skips a finished job
    # Even a direct call (redelivery that bypasses the job check) records nothing new.
    result = csv_import.run_csv_import(Job.objects.get(pk=job.pk), str(batch.pk))
    assert result["status"] == "completed"
    batch.refresh_from_db()
    assert batch.created_count == 6
    assert (Company.objects.count(), ImportRow.objects.count(), AuditLog.objects.count()) == before


def test_a_crash_midway_is_retried_and_the_retry_finishes_the_batch(
    api: APIClient, campaign: Campaign, queued: list[Any], monkeypatch: Any, settings: Any
) -> None:
    settings.IMPORT_CHUNK_SIZE = 2
    batch, job = accepted(upload(api, campaign, make_csv(8)))
    real = csv_import._handle_row
    crashed: list[int] = []

    def flaky(batch_: ImportBatch, row: Any, user: Any) -> None:
        if row.row_number == 5 and not crashed:
            crashed.append(row.row_number)
            raise RuntimeError("worker died")
        real(batch_, row, user)

    monkeypatch.setattr(csv_import, "_handle_row", flaky)
    run_worker(job.pk, batch.pk)  # attempt 1: the wrapper requeues the job

    batch.refresh_from_db()
    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert batch.status == ImportBatchStatus.PROCESSING
    assert batch.processed_count == 4
    assert stored_files() == [f"{batch.pk}.csv"]  # still there for the retry

    run_worker(job.pk, batch.pk)  # attempt 2
    batch.refresh_from_db()
    job.refresh_from_db()
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.created_count == 8
    assert Company.objects.count() == 8
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert (job.done_count, job.failed_count) == (8, 0)
    assert stored_files() == []


def test_when_the_attempts_run_out_the_batch_fails_and_the_file_goes(
    api: APIClient, campaign: Campaign, queued: list[Any], monkeypatch: Any
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(4)))

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(csv_import, "_handle_row", broken)
    run_worker(job.pk, batch.pk)
    run_worker(job.pk, batch.pk)
    with pytest.raises(RuntimeError):
        run_worker(job.pk, batch.pk)  # the last attempt re-raises for Django-Q2

    batch.refresh_from_db()
    job.refresh_from_db()
    assert batch.status == ImportBatchStatus.FAILED
    assert "repeated errors (RuntimeError)" in batch.error_summary
    assert "database went away" not in batch.error_summary
    assert job.status == JobStatus.FAILED
    assert stored_files() == []


def test_one_unexpected_row_error_does_not_stop_the_batch(
    api: APIClient, campaign: Campaign, queued: list[Any], monkeypatch: Any
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(5)))
    real = import_services.process_row

    def process(batch_: ImportBatch, number: int, raw: Any, **kwargs: Any) -> Any:
        if number == 3:
            raise RuntimeError("secret detail: Company 3")
        return real(batch_, number, raw, **kwargs)

    monkeypatch.setattr(csv_import, "process_row", process)
    run_worker(job.pk, batch.pk)
    batch.refresh_from_db()
    assert batch.status == ImportBatchStatus.PARTIAL
    assert (batch.created_count, batch.failed_count) == (4, 1)
    row = ImportRow.objects.get(batch=batch, row_number=3)
    assert row.error_code == "internal_error"
    assert "secret detail" not in row.error_message


def test_a_missing_upload_fails_the_batch_with_a_reason(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(3)))
    uploads.delete_upload(batch)
    run_worker(job.pk, batch.pk)
    batch.refresh_from_db()
    job.refresh_from_db()
    assert batch.status == ImportBatchStatus.FAILED
    assert "no longer available" in batch.error_summary
    assert job.status == JobStatus.FAILED


def test_a_whole_file_problem_found_by_the_worker_fails_the_batch_but_keeps_recorded_rows(
    api: APIClient, campaign: Campaign, queued: list[Any], monkeypatch: Any
) -> None:
    batch, job = accepted(upload(api, campaign, make_csv(6)))

    class Broken:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.count = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def __enter__(self) -> Broken:
            return self

        def __exit__(self, *exc: Any) -> None:
            self.inner.close()

        def __iter__(self) -> Broken:
            return self

        def __next__(self) -> Any:
            self.count += 1
            if self.count == 4:
                raise CsvFileError("undecodable", "utf-8")
            return next(self.inner)

    real = iter_rows
    monkeypatch.setattr(csv_import, "iter_rows", lambda *a, **k: Broken(real(*a, **k)))
    run_worker(job.pk, batch.pk)
    batch.refresh_from_db()
    job.refresh_from_db()
    assert batch.status == ImportBatchStatus.FAILED
    assert "could not be decoded" in batch.error_summary
    assert batch.created_count == 3  # what was recorded stays
    assert Company.objects.count() == 3
    assert job.status == JobStatus.PARTIAL  # three rows went in, then the run itself broke
    assert job.failed_count == 1
    assert stored_files() == []


def test_the_task_refuses_a_batch_of_another_client(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    batch, _ = accepted(upload(api, campaign, make_csv(2)))
    foreign_job = Job.objects.create(type="process_csv_import", client=make_client())
    with pytest.raises(ValueError, match="does not belong"):
        csv_import.run_csv_import(foreign_job, str(batch.pk))


# ------------------------------------------------------------------ files and privacy


def test_the_stored_file_has_a_random_name_and_the_original_name_is_only_a_label(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    batch, _ = accepted(upload(api, campaign, make_csv(2), name="../../etc/evil name.csv"))
    assert batch.original_filename == "evil name.csv"
    assert stored_files() == [f"{batch.pk}.csv"]
    storage = uploads.upload_storage()
    assert storage.size(f"{batch.pk}.csv") == batch.file_size


def test_a_failed_submission_leaves_no_file_behind(
    api: APIClient, campaign: Campaign, monkeypatch: Any
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("queue is down")

    monkeypatch.setattr(csv_import, "async_task", boom)
    api.raise_request_exception = False
    response = upload(api, campaign, make_csv(3))
    assert response.status_code == 500
    assert stored_files() == []
    assert not ImportBatch.objects.exists()  # the batch rolled back with the queue entry
    assert not Job.objects.exists()


def test_purge_stale_uploads(campaign: Campaign) -> None:
    from datetime import timedelta

    live = make_import_batch_with_file(campaign, ImportBatchStatus.PENDING)
    done = make_import_batch_with_file(campaign, ImportBatchStatus.PENDING)
    from apps.imports.services import cancel_batch

    cancel_batch(done)
    orphan = uuid.uuid4()
    uploads.upload_storage().save(f"{orphan}.csv", io.BytesIO(b"x"))
    uploads.upload_storage().save("notes.txt", io.BytesIO(b"keep me"))

    report = uploads.purge_stale_uploads()
    assert report.removed == 2  # the cancelled batch's file and the orphan
    assert sorted(stored_files()) == sorted([f"{live.pk}.csv", "notes.txt"])

    # A pending batch whose worker never came back is swept once old enough.
    from django.utils import timezone

    report = uploads.purge_stale_uploads(now=timezone.now() + timedelta(days=2))
    assert report.removed == 1
    assert stored_files() == ["notes.txt"]


def test_cleanup_command(campaign: Campaign) -> None:
    from django.core.management import call_command

    done = make_import_batch_with_file(campaign, ImportBatchStatus.PENDING)
    from apps.imports.services import cancel_batch

    cancel_batch(done)
    out = io.StringIO()
    call_command("cleanup_import_uploads", stdout=out)
    assert "Removed 1 file(s)" in out.getvalue()
    assert stored_files() == []


def make_import_batch_with_file(campaign: Campaign, status: str) -> ImportBatch:
    from apps.imports.services import create_batch

    batch = create_batch(
        campaign, "csv", file_sha256=uuid.uuid4().hex + uuid.uuid4().hex, total_count=1
    )
    uploads.save_upload(batch, io.BytesIO(b"company_name\nx\n"))
    return batch


def test_logs_carry_ids_and_counts_but_no_file_content(
    api: APIClient, campaign: Campaign, caplog: pytest.LogCaptureFixture
) -> None:
    content = b"company_name,website\nZyxwv Distinctive Name,zyxwv.example.com\n,bad.example.com\n"
    with caplog.at_level("DEBUG"):
        accepted(upload(api, campaign, content, name="secret-client-list.csv"))
    text = caplog.text
    assert "Zyxwv" not in text
    assert "zyxwv.example.com" not in text
    assert "secret-client-list" not in text
    assert "CSV import" in text  # but it does log


# ------------------------------------------------------------------ audit


def test_audit_entries_for_the_batch(api: APIClient, campaign: Campaign, manager: User) -> None:
    batch, _ = accepted(upload(api, campaign, make_csv(3)))
    entries = list(
        AuditLog.objects.filter(object_type="import_batch", object_id=batch.pk).order_by(
            "created_at"
        )
    )
    assert [e.action for e in entries] == [AuditAction.CREATE, AuditAction.UPDATE]
    created, finished = entries
    assert created.actor_id == manager.pk
    assert created.client_id == campaign.client_id
    assert created.after is not None
    assert finished.before is not None
    assert finished.after is not None
    assert created.after["source"] == "csv"
    assert created.after["total_count"] == 3
    assert created.after["file_sha256"] == batch.file_sha256
    assert finished.actor_id is None  # the worker
    assert finished.before["status"] == "pending"
    assert finished.after["status"] == "completed"
    assert finished.after["created_count"] == 3


# ------------------------------------------------------------------ roles and tenancy


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix(world: tuple[Client, Campaign], role: str, queued: list[Any]) -> None:
    client, campaign = world
    api = as_user(user_with(role, client))
    allowed = role in EDIT_ROLES
    for preview in (True, False):
        response = upload(api, campaign, make_csv(2, start=1 if preview else 50), preview=preview)
        assert response.status_code == (200 if preview else 202) if allowed else 403, (
            role,
            preview,
            response.content,
        )
        if not allowed:
            assert response.json()["error"]["code"] == "permission_denied"
    if not allowed:
        assert not ImportBatch.objects.exists()


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager", "admin"])
def test_cancel_needs_the_edit_level(
    world: tuple[Client, Campaign], role: str, queued: list[Any]
) -> None:
    client, campaign = world
    batch, _ = accepted(upload(as_user(user_with("manager", client)), campaign, make_csv(2)))
    response = as_user(user_with(role, client)).post(cancel_url(batch))
    assert response.status_code == (200 if role in {"manager", "admin"} else 403)


def test_anonymous_requests_get_401(
    api_client: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    for preview in (True, False):
        response = upload(api_client, campaign, make_csv(2), preview=preview)
        assert response.status_code == 401
    assert api_client.post(cancel_url(uuid.uuid4())).status_code == 401


def test_another_clients_campaign_and_batch_are_404(
    world: tuple[Client, Campaign], queued: list[Any]
) -> None:
    client, campaign = world
    owner = as_user(user_with("admin", client))
    batch, _ = accepted(upload(owner, campaign, make_csv(2)))

    stranger_client = make_client()
    stranger = as_user(user_with("admin", stranger_client))
    missing = uuid.uuid4()
    for preview in (True, False):
        real = upload(stranger, campaign, make_csv(2), preview=preview)
        gone = upload(
            stranger,
            Campaign(pk=missing),
            make_csv(2),
            preview=preview,
        )
        assert real.status_code == gone.status_code == 404
        assert real.json()["error"]["code"] == gone.json()["error"]["code"] == "not_found"
    assert stranger.post(cancel_url(batch)).status_code == 404
    assert stranger.post(cancel_url(missing)).status_code == 404
    batch.refresh_from_db()
    assert batch.status == ImportBatchStatus.PENDING
    assert ImportBatch.objects.count() == 1


def test_a_global_admin_can_import_into_any_campaign(world: tuple[Client, Campaign]) -> None:
    api = as_user(make_user(is_superuser=True))
    batch, _ = accepted(upload(api, world[1], make_csv(2)))
    assert batch.status == ImportBatchStatus.COMPLETED


def test_an_archived_campaign_takes_no_import(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    campaign_services.archive_campaign(campaign)
    response = upload(api, campaign, make_csv(2))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert not ImportBatch.objects.exists()
    assert stored_files() == []
    assert queued == []


def test_an_archived_client_takes_no_import(
    api: APIClient, world: tuple[Client, Campaign], queued: list[Any]
) -> None:
    campaign_services.archive_client(world[0])
    response = upload(api, world[1], make_csv(2))
    assert response.status_code in {400, 404}
    assert not ImportBatch.objects.exists()
    assert stored_files() == []


def test_the_imported_companies_belong_to_the_campaign_and_client(
    api: APIClient, world: tuple[Client, Campaign]
) -> None:
    client, campaign = world
    other = make_campaign(client=client, name="Other")
    accepted(upload(api, campaign, make_csv(3)))
    assert Company.objects.filter(campaign=campaign, client=client).count() == 3
    assert Company.objects.filter(campaign=other).count() == 0


# ------------------------------------------------------------------ cost


def count_queries(fn: Callable[[], Any]) -> int:
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return len(ctx)


def test_the_request_does_a_constant_number_of_queries(
    api: APIClient, campaign: Campaign, queued: list[Any]
) -> None:
    small = count_queries(lambda: upload(api, campaign, make_csv(3)))
    large = count_queries(lambda: upload(api, campaign, make_csv(300, start=100)))
    assert large == small  # accepting a file costs the same whatever its size
    assert small < 40


def test_processing_costs_a_bounded_number_of_queries_per_row(
    api: APIClient, campaign: Campaign, queued: list[Any], settings: Any
) -> None:
    settings.IMPORT_CHUNK_SIZE = 50
    a, job_a = accepted(upload(api, campaign, make_csv(20)))
    b, job_b = accepted(upload(api, campaign, make_csv(60, start=100)))
    q_small = count_queries(lambda: run_worker(job_a.pk, a.pk))
    q_large = count_queries(lambda: run_worker(job_b.pk, b.pk))
    per_row = (q_large - q_small) / 40
    assert per_row < 30, per_row  # no growth beyond a fixed cost per row (no N+1 over the file)


def test_preview_is_cheap(api: APIClient, campaign: Campaign) -> None:
    small = count_queries(lambda: upload(api, campaign, make_csv(5), preview=True))
    large = count_queries(lambda: upload(api, campaign, make_csv(2000), preview=True))
    assert small == large
