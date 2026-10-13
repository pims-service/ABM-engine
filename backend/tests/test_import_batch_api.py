"""Import batch status, row results, error report and history (issue #63).

Batches are made by the real CSV import (Q_CLUSTER is in sync mode under the test settings, so
``POST .../csv-import/`` runs the whole import inline) or straight through the factories.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, Client
from apps.imports import csv_import, error_report, uploads
from apps.imports.api.batch_serializers import (
    RAW_MAX_KEYS,
    RAW_MAX_VALUE_CHARS,
    capped_raw_data,
)
from apps.imports.models import ImportBatch, ImportBatchStatus, ImportRow
from tests.factories import make_campaign, make_client, make_company, make_user
from tests.factories_imports import make_import_batch, make_import_row

pytestmark = [pytest.mark.api, pytest.mark.django_db]

READERS = ["viewer", "reviewer", "manager", "admin", "global"]

FORMULA_NAME = '=HYPERLINK("http://evil.example","click")'
MIXED_CSV = (
    "company_name,website,country,industry\n"
    "Alpha Co,alpha.example.com,SA,Retail\n"  # 1 created
    "Alpha Again,alpha.example.com,SA,Retail\n"  # 2 duplicate (same domain)
    "Beta Trading,beta-one.example.com,SA,Retail\n"  # 3 created
    "Beta Trading,,SA,Retail\n"  # 4 created, weak duplicate of row 3 (same name, no domain)
    f'"{FORMULA_NAME.replace(chr(34), chr(34) * 2)}",bad site,SA,Retail\n'  # 5 failed
    "Gamma Ltd,@SUM(1+1),SA,Retail\n"  # 6 failed: website invalid
    "+Delta,-1+1,,Retail\n"  # 7 failed: website invalid
).encode()


# ------------------------------------------------------------------ helpers


def as_user(user: User | None) -> APIClient:
    api = APIClient()
    if user is not None:
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
def campaign(world: tuple[Client, Campaign]) -> Campaign:
    return world[1]


@pytest.fixture
def manager(world: tuple[Client, Campaign]) -> User:
    return user_with("manager", world[0])


@pytest.fixture
def api(manager: User) -> APIClient:
    return as_user(manager)


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Iterator[None]:
    storage = uploads.upload_storage()
    yield
    try:
        for name in storage.listdir("")[1]:
            storage.delete(name)
    except FileNotFoundError:
        pass


def start_import(api: APIClient, campaign: Campaign, content: bytes, name: str = "c.csv") -> str:
    upload = SimpleUploadedFile(name, content, content_type="text/csv")
    response = api.post(
        f"/api/v1/campaigns/{campaign.pk}/csv-import/", {"file": upload}, format="multipart"
    )
    assert response.status_code == 202, response.content
    return str(response.json()["batch"]["id"])


@pytest.fixture
def mixed(api: APIClient, campaign: Campaign) -> str:
    """A finished CSV batch with every kind of outcome."""
    return start_import(api, campaign, MIXED_CSV)


def detail_url(batch_id: Any) -> str:
    return f"/api/v1/imports/{batch_id}/"


def rows_url(batch_id: Any, query: str = "") -> str:
    return f"/api/v1/imports/{batch_id}/rows/{'?' + query if query else ''}"


def report_url(batch_id: Any, query: str = "") -> str:
    return f"/api/v1/imports/{batch_id}/errors.csv{'?' + query if query else ''}"


def history_url(campaign: Campaign | str, query: str = "") -> str:
    pk = campaign.pk if isinstance(campaign, Campaign) else campaign
    return f"/api/v1/campaigns/{pk}/imports/{'?' + query if query else ''}"


def report_text(response: Any) -> str:
    assert response.status_code == 200, response.content
    return b"".join(response.streaming_content).decode("utf-8")


def report_table(response: Any) -> list[list[str]]:
    text = report_text(response)
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def row_numbers(api: APIClient, batch_id: Any, query: str = "") -> list[int]:
    response = api.get(rows_url(batch_id, query + ("&" if query else "") + "page_size=100"))
    assert response.status_code == 200, response.content
    return [r["row_number"] for r in response.json()["results"]]


# ------------------------------------------------------------------ detail (the status_url)


def test_the_status_url_of_the_202_serves_the_batch(api: APIClient, campaign: Campaign) -> None:
    upload = SimpleUploadedFile("c.csv", MIXED_CSV, content_type="text/csv")
    accepted = api.post(
        f"/api/v1/campaigns/{campaign.pk}/csv-import/", {"file": upload}, format="multipart"
    )
    status_url = accepted.json()["status_url"]
    response = api.get(status_url)
    assert response.status_code == 200
    assert response.json()["id"] == accepted.json()["batch"]["id"]


def test_detail_has_status_counters_progress_job_and_summary(
    api: APIClient, mixed: str, campaign: Campaign
) -> None:
    body = api.get(detail_url(mixed)).json()
    batch = ImportBatch.objects.get(pk=mixed)
    assert body["status"] == "partial"
    assert body["source"] == "csv"
    assert body["campaign"] == str(campaign.pk)
    assert body["campaign_name"] == campaign.name
    assert body["original_filename"] == "c.csv"
    assert body["total_count"] == 7
    assert (body["created_count"], body["duplicate_count"], body["failed_count"]) == (3, 1, 3)
    assert body["processed_count"] == 7
    assert body["progress_percent"] == 100
    assert body["job"] == str(batch.job_id)
    assert body["job_status"] == batch.job.status
    assert body["finished_at"] is not None
    assert body["summary"]["outcomes"] == {
        "created": 3,
        "duplicate": 1,
        "restored": 0,
        "skipped": 0,
        "failed": 3,
    }
    assert body["summary"]["weak_duplicates"] == 1
    codes = body["summary"]["error_codes"]
    assert sum(c["count"] for c in codes) == 3
    assert [c["count"] for c in codes] == sorted((c["count"] for c in codes), reverse=True)
    assert all(c["code"] for c in codes)


def test_counters_always_match_the_row_outcomes(api: APIClient, mixed: str) -> None:
    body = api.get(detail_url(mixed)).json()
    by_outcome: dict[str, int] = {}
    for row in ImportRow.objects.filter(batch_id=mixed):
        by_outcome[row.outcome] = by_outcome.get(row.outcome, 0) + 1
    outcomes = body["summary"]["outcomes"]
    assert {k: v for k, v in outcomes.items() if v} == by_outcome
    assert sum(outcomes.values()) == body["processed_count"] == body["total_count"]


def test_detail_of_a_manual_batch_has_no_job(api: APIClient, campaign: Campaign) -> None:
    batch = make_import_batch(campaign=campaign, source="manual", total_count=1)
    body = api.get(detail_url(batch.pk)).json()
    assert body["status"] == "pending"
    assert body["job"] is None
    assert body["job_status"] is None
    assert body["progress_percent"] == 0
    assert body["summary"]["error_codes"] == []


def test_the_detail_shows_the_status_transitions_while_the_import_runs(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync mode: poll the status url from inside the worker, before each row is processed."""
    seen: list[tuple[str, int, int]] = []
    original = csv_import.process_row

    def spy(batch: ImportBatch, *args: Any, **kwargs: Any) -> Any:
        body = api.get(detail_url(batch.pk)).json()
        seen.append((body["status"], body["processed_count"], body["progress_percent"]))
        return original(batch, *args, **kwargs)

    monkeypatch.setattr(csv_import, "process_row", spy)
    batch_id = start_import(api, campaign, MIXED_CSV)
    assert seen, "the spy never ran"
    # Rows 1-4 are valid; the three broken ones fail before process_row is called.
    assert [s[0] for s in seen][1:] == ["processing"] * (len(seen) - 1)
    assert [s[1] for s in seen] == sorted(s[1] for s in seen)
    assert seen[-1][1] > seen[0][1]
    assert 0 < seen[-1][2] < 100
    assert api.get(detail_url(batch_id)).json()["status"] == ImportBatchStatus.PARTIAL


def test_a_queued_batch_reads_pending_with_no_progress(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(csv_import, "async_task", lambda *a, **k: "task-1")
    batch_id = start_import(api, campaign, MIXED_CSV)
    body = api.get(detail_url(batch_id)).json()
    assert body["status"] == "pending"
    assert body["processed_count"] == 0
    assert body["progress_percent"] == 0
    assert body["finished_at"] is None
    assert body["job_status"] == "queued"


def test_cancel_still_works_and_the_detail_shows_it(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(csv_import, "async_task", lambda *a, **k: "task-1")
    batch_id = start_import(api, campaign, MIXED_CSV)
    assert api.post(f"/api/v1/imports/{batch_id}/cancel/").status_code == 200
    assert api.get(detail_url(batch_id)).json()["status"] == "cancelled"


# ------------------------------------------------------------------ roles and tenant isolation


@pytest.mark.parametrize("role", READERS)
def test_every_reader_role_can_read_all_three_batch_routes(
    role: str, world: tuple[Client, Campaign], mixed: str, campaign: Campaign
) -> None:
    api = as_user(user_with(role, world[0]))
    assert api.get(detail_url(mixed)).status_code == 200
    assert api.get(rows_url(mixed)).status_code == 200
    assert api.get(report_url(mixed)).status_code == 200
    assert api.get(history_url(campaign)).status_code == 200


def test_there_are_no_write_routes_here(api: APIClient, mixed: str, campaign: Campaign) -> None:
    for url in (detail_url(mixed), rows_url(mixed), report_url(mixed), history_url(campaign)):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(api, method)(url).status_code == 405, (method, url)


def test_anonymous_requests_are_401(mixed: str, campaign: Campaign) -> None:
    api = as_user(None)
    for url in (detail_url(mixed), rows_url(mixed), report_url(mixed), history_url(campaign)):
        response = api.get(url)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"


def test_another_clients_batch_and_campaign_are_404_on_every_route(
    mixed: str, campaign: Campaign
) -> None:
    stranger = as_user(user_with("admin", make_client()))
    missing = str(uuid.uuid4())
    for real, fake in (
        (detail_url(mixed), detail_url(missing)),
        (rows_url(mixed), rows_url(missing)),
        (report_url(mixed), report_url(missing)),
        (history_url(campaign), history_url(missing)),
    ):
        foreign, absent = stranger.get(real), stranger.get(fake)
        assert foreign.status_code == absent.status_code == 404, real
        assert foreign.json()["error"]["code"] == "not_found"


def test_a_stranger_gets_no_rows_even_with_filters(mixed: str) -> None:
    stranger = as_user(user_with("admin", make_client()))
    assert stranger.get(rows_url(mixed, "outcome=failed&search=Gamma")).status_code == 404
    assert stranger.get(report_url(mixed, "include=duplicates")).status_code == 404


def test_the_history_never_lists_another_campaigns_batches(
    api: APIClient, world: tuple[Client, Campaign], mixed: str
) -> None:
    other = make_campaign(client=world[0], name="Other campaign")
    other_batch = make_import_batch(campaign=other, source="manual")
    ids = [b["id"] for b in api.get(history_url(world[1])).json()["results"]]
    assert mixed in ids
    assert str(other_batch.pk) not in ids


# ------------------------------------------------------------------ history list


def history(api: APIClient, campaign: Campaign, query: str = "") -> list[dict[str, Any]]:
    response = api.get(history_url(campaign, query))
    assert response.status_code == 200, response.content
    return list(response.json()["results"])


def test_history_is_newest_first_with_counters(api: APIClient, campaign: Campaign) -> None:
    now = timezone.now()
    batches = [make_import_batch(campaign=campaign, source="manual") for _ in range(3)]
    for age, batch in zip((3, 1, 2), batches, strict=True):
        ImportBatch.objects.filter(pk=batch.pk).update(created_at=now - timedelta(days=age))
    results = history(api, campaign)
    assert [r["id"] for r in results] == [str(batches[i].pk) for i in (1, 2, 0)]
    assert {"status", "total_count", "created_count", "failed_count", "progress_percent"} <= set(
        results[0]
    )


def test_history_filters(api: APIClient, campaign: Campaign, mixed: str, manager: User) -> None:
    manual = make_import_batch(campaign=campaign, source="manual", created_by=manager)
    provider = make_import_batch(campaign=campaign, source="provider", created_by=None)
    assert {r["id"] for r in history(api, campaign)} == {mixed, str(manual.pk), str(provider.pk)}
    assert [r["id"] for r in history(api, campaign, "source=csv")] == [mixed]
    assert [r["id"] for r in history(api, campaign, "source=provider")] == [str(provider.pk)]
    assert [r["id"] for r in history(api, campaign, "status=partial")] == [mixed]
    assert {r["id"] for r in history(api, campaign, "status=pending")} == {
        str(manual.pk),
        str(provider.pk),
    }
    assert {r["id"] for r in history(api, campaign, f"created_by={manager.pk}")} == {
        mixed,
        str(manual.pk),
    }
    assert history(api, campaign, f"created_by={uuid.uuid4()}") == []


def test_history_date_range_is_inclusive(api: APIClient, campaign: Campaign) -> None:
    old = make_import_batch(campaign=campaign)
    new = make_import_batch(campaign=campaign)
    when = timezone.now() - timedelta(days=10)
    ImportBatch.objects.filter(pk=old.pk).update(created_at=when)
    day = timezone.localdate(when).isoformat()
    today = timezone.localdate().isoformat()
    assert [r["id"] for r in history(api, campaign, f"created_to={day}")] == [str(old.pk)]
    assert [r["id"] for r in history(api, campaign, f"created_from={today}")] == [str(new.pk)]
    both = history(api, campaign, f"created_from={day}&created_to={today}")
    assert {r["id"] for r in both} == {str(old.pk), str(new.pk)}


def test_history_ordering(api: APIClient, campaign: Campaign) -> None:
    small = make_import_batch(campaign=campaign, total_count=1)
    big = make_import_batch(campaign=campaign, total_count=50)
    assert [r["id"] for r in history(api, campaign, "ordering=-total_count")] == [
        str(big.pk),
        str(small.pk),
    ]
    assert [r["id"] for r in history(api, campaign, "ordering=total_count")] == [
        str(small.pk),
        str(big.pk),
    ]


def test_history_is_paginated(api: APIClient, campaign: Campaign) -> None:
    for _ in range(5):
        make_import_batch(campaign=campaign)
    first = api.get(history_url(campaign, "page_size=2")).json()
    assert first["count"] == 5
    assert len(first["results"]) == 2
    assert first["next"]
    last = api.get(history_url(campaign, "page_size=2&page=3")).json()
    assert len(last["results"]) == 1
    assert last["next"] is None


@pytest.mark.parametrize(
    "query",
    [
        "source=email",
        "status=done",
        "created_by=not-a-uuid",
        "created_from=yesterday",
        "ordering=name",
        "created_from=2026-02-01&created_to=2026-01-01",
    ],
)
def test_history_rejects_bad_filters_with_the_error_envelope(
    api: APIClient, campaign: Campaign, query: str
) -> None:
    response = api.get(history_url(campaign, query))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------------ rows


def test_rows_are_in_file_order_with_the_documented_shape(api: APIClient, mixed: str) -> None:
    body = api.get(rows_url(mixed)).json()
    assert body["count"] == 7
    assert [r["row_number"] for r in body["results"]] == [1, 2, 3, 4, 5, 6, 7]
    first = body["results"][0]
    assert set(first) == {
        "id",
        "row_number",
        "outcome",
        "match_strength",
        "matched_on",
        "name",
        "website",
        "domain",
        "profile_url",
        "country",
        "error_code",
        "error_message",
        "company",
        "candidate",
        "raw_data",
    }
    assert first["outcome"] == "created"
    assert first["name"] == "Alpha Co"
    assert first["domain"] == "alpha.example.com"
    assert first["company"]["name"] == "Alpha Co"
    assert set(first["company"]) == {"id", "name", "domain"}
    assert first["candidate"] is None
    assert first["raw_data"]["industry"] == "Retail"


def test_rows_show_the_duplicate_match_and_the_possible_duplicate(
    api: APIClient, mixed: str
) -> None:
    rows = {r["row_number"]: r for r in api.get(rows_url(mixed)).json()["results"]}
    dup = rows[2]
    assert dup["outcome"] == "duplicate"
    assert dup["match_strength"] == "strong"
    assert dup["matched_on"] == "domain"
    assert dup["company"]["id"] == dup["candidate"]["id"] == rows[1]["company"]["id"]
    weak = rows[4]
    assert weak["outcome"] == "created"
    assert weak["match_strength"] == "weak"
    assert weak["candidate"]["id"] == rows[3]["company"]["id"]
    assert weak["company"]["id"] != weak["candidate"]["id"]


def test_failed_rows_carry_a_code_and_message_and_no_company(api: APIClient, mixed: str) -> None:
    rows = api.get(rows_url(mixed, "outcome=failed")).json()["results"]
    assert [r["row_number"] for r in rows] == [5, 6, 7]
    for row in rows:
        assert row["error_code"]
        assert row["error_message"]
        assert row["company"] is None


def test_rows_filters(api: APIClient, mixed: str) -> None:
    assert row_numbers(api, mixed, "outcome=created") == [1, 3, 4]
    assert row_numbers(api, mixed, "outcome=duplicate") == [2]
    assert row_numbers(api, mixed, "outcome=restored") == []
    assert row_numbers(api, mixed, "has_error=true") == [5, 6, 7]
    assert row_numbers(api, mixed, "has_error=false") == [1, 2, 3, 4]
    assert row_numbers(api, mixed, "match_strength=weak") == [4]
    assert row_numbers(api, mixed, "match_strength=strong") == [2]
    code = api.get(rows_url(mixed, "outcome=failed")).json()["results"][1]["error_code"]
    expected = [
        r["row_number"]
        for r in api.get(rows_url(mixed, "outcome=failed")).json()["results"]
        if r["error_code"] == code
    ]
    assert row_numbers(api, mixed, f"error_code={code}") == expected
    assert row_numbers(api, mixed, "error_code=no_such_code") == []
    assert row_numbers(api, mixed, "outcome=failed&has_error=false") == []


def test_rows_search_matches_name_and_domain_case_insensitively(api: APIClient, mixed: str) -> None:
    assert row_numbers(api, mixed, "search=beta") == [3, 4]
    assert row_numbers(api, mixed, "search=BETA-ONE") == [3]
    assert row_numbers(api, mixed, "search=alpha.example") == [1, 2]
    assert row_numbers(api, mixed, "search=gamma") == []  # unnormalised (failed) rows
    assert row_numbers(api, mixed, "search=zzz") == []


def test_rows_ordering(api: APIClient, mixed: str) -> None:
    assert row_numbers(api, mixed, "ordering=-row_number") == [7, 6, 5, 4, 3, 2, 1]
    assert row_numbers(api, mixed, "ordering=row_number") == [1, 2, 3, 4, 5, 6, 7]


def test_rows_are_paginated(api: APIClient, mixed: str) -> None:
    first = api.get(rows_url(mixed, "page_size=3")).json()
    assert first["count"] == 7
    assert [r["row_number"] for r in first["results"]] == [1, 2, 3]
    assert first["next"]
    assert first["previous"] is None
    third = api.get(rows_url(mixed, "page_size=3&page=3")).json()
    assert [r["row_number"] for r in third["results"]] == [7]
    assert third["next"] is None
    assert api.get(rows_url(mixed, "page_size=3&page=4")).status_code == 404
    capped = api.get(rows_url(mixed, "page_size=1000")).json()
    assert len(capped["results"]) == 7  # the cap is 100


@pytest.mark.parametrize(
    "query",
    [
        "outcome=weird",
        "has_error=maybe",
        "match_strength=medium",
        "ordering=name",
        "search=" + "x" * 201,
    ],
)
def test_rows_reject_bad_filters(api: APIClient, mixed: str, query: str) -> None:
    response = api.get(rows_url(mixed, query))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_rows_are_visible_while_the_batch_is_still_running(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    original = csv_import.process_row

    def spy(batch: ImportBatch, *args: Any, **kwargs: Any) -> Any:
        seen.append(api.get(rows_url(batch.pk)).json()["count"])
        return original(batch, *args, **kwargs)

    monkeypatch.setattr(csv_import, "process_row", spy)
    start_import(api, campaign, MIXED_CSV)
    assert seen == sorted(seen)
    assert seen[-1] > seen[0]


def test_raw_data_is_the_stored_sanitised_value_and_capped() -> None:
    raw = {f"col{i}": "x" * 1000 for i in range(RAW_MAX_KEYS + 10)}
    capped = capped_raw_data(raw)
    assert len(capped) == RAW_MAX_KEYS + 1
    assert capped["_truncated"] is True
    assert len(capped["col0"]) == RAW_MAX_VALUE_CHARS
    small = capped_raw_data({"name": "Acme", "n": 3, "nested": {"a": 1}})
    assert small == {"name": "Acme", "n": 3, "nested": "{'a': 1}"}
    assert "_truncated" not in small
    assert capped_raw_data({"a": "b", "_truncated": True})["_truncated"] is True
    assert capped_raw_data(["not", "a", "dict"]) == {}


def test_a_credential_in_the_submitted_data_never_reaches_the_rows_endpoint(
    api: APIClient, campaign: Campaign
) -> None:
    batch = make_import_batch(campaign=campaign, total_count=1)
    make_import_row(
        batch=batch,
        row_number=1,
        raw_data={"name": "Acme", "api_key": "YOUR_API_KEY", "password": "YOUR_PASSWORD"},
    )
    body = api.get(rows_url(batch.pk)).json()["results"][0]["raw_data"]
    assert body["api_key"] == "[REDACTED]"
    assert body["password"] == "[REDACTED]"
    assert body["name"] == "Acme"


# ------------------------------------------------------------------ the error report


def test_the_report_download_headers(api: APIClient, mixed: str) -> None:
    response = api.get(report_url(mixed))
    assert response.status_code == 200
    assert response.streaming is True
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == f'attachment; filename="import-{mixed}-errors.csv"'
    assert "no-store" in response["Cache-Control"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert next(iter(response.streaming_content)).startswith(b"\xef\xbb\xbf")


def test_the_report_lists_the_failed_rows_as_entered(api: APIClient, mixed: str) -> None:
    table = report_table(api.get(report_url(mixed)))
    assert table[0] == [
        "row_number",
        "name",
        "website",
        "profile_url",
        "country",
        "outcome",
        "error_code",
        "error_message",
    ]
    assert [r[0] for r in table[1:]] == ["5", "6", "7"]
    by_row = {r[0]: r for r in table[1:]}
    assert by_row["6"][1] == "Gamma Ltd"
    assert by_row["6"][2] == "'@SUM(1+1)"  # as entered, neutralised
    assert by_row["6"][4] == "SA"
    assert all(r[5] == "failed" and r[6] and r[7] for r in table[1:])


def test_every_cell_of_the_report_is_protected_against_formulas(api: APIClient, mixed: str) -> None:
    table = report_table(api.get(report_url(mixed)))
    by_row = {r[0]: r for r in table[1:]}
    assert by_row["5"][1] == "'" + FORMULA_NAME
    assert by_row["7"][1] == "'+Delta"
    for line in table[1:]:
        assert not any(cell[:1] in {"=", "+", "-", "@", "\t", "\r"} for cell in line), line


def test_formula_payloads_in_every_column_are_neutralised(
    api: APIClient, campaign: Campaign
) -> None:
    batch = make_import_batch(campaign=campaign, total_count=1)
    payloads = ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1", "\r=1", chr(0xFF1D) + "1+1", "  =1+1"]
    for n, payload in enumerate(payloads, start=1):
        make_import_row(
            batch=batch,
            row_number=n,
            outcome="failed",
            error_code="website_invalid",
            error_message=payload,
            name=payload,
            raw_data={
                "name": payload,
                "website": payload,
                "profile_url": payload,
                "country": payload,
            },
        )
    ImportBatch.objects.filter(pk=batch.pk).update(total_count=len(payloads))
    table = report_table(api.get(report_url(batch.pk)))
    for line in table[1:]:
        for cell in (line[1], line[2], line[3], line[4], line[7]):
            assert cell.startswith("'"), line
    assert len(table) == len(payloads) + 1


def test_the_report_is_utf8_with_bom_and_keeps_arabic(api: APIClient, campaign: Campaign) -> None:
    batch = make_import_batch(campaign=campaign, total_count=1)
    make_import_row(
        batch=batch,
        row_number=1,
        outcome="failed",
        error_code="website_invalid",
        error_message="الموقع غير صالح",
        raw_data={"name": "شركة المثال", "website": "bad site", "country": "SA"},
    )
    raw = b"".join(api.get(report_url(batch.pk)).streaming_content)
    assert raw.startswith(b"\xef\xbb\xbf")
    row = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))[1]
    assert row[1] == "شركة المثال"
    assert row[7] == "الموقع غير صالح"


def test_include_duplicates_adds_the_rows_that_matched_an_existing_company(
    api: APIClient, mixed: str
) -> None:
    table = report_table(api.get(report_url(mixed, "include=duplicates")))
    assert [r[0] for r in table[1:]] == ["2", "5", "6", "7"]
    assert table[1][5] == "duplicate"
    assert table[1][1] == "Alpha Again"
    assert "weak" not in report_text(api.get(report_url(mixed, "include=duplicates")))
    # A weak match was still created: it is never an error row.
    assert "4" not in [r[0] for r in table[1:]]


def test_a_bad_include_is_a_validation_error(api: APIClient, mixed: str) -> None:
    response = api.get(report_url(mixed, "include=everything"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_a_batch_with_no_problems_gets_a_header_only_report(
    api: APIClient, campaign: Campaign
) -> None:
    batch_id = start_import(api, campaign, b"company_name,website\nOk Co,ok.example.com\n")
    table = report_table(api.get(report_url(batch_id)))
    assert len(table) == 1
    assert table[0][0] == "row_number"


def test_rows_rejected_before_normalising_are_reported_with_the_file_columns(
    api: APIClient, campaign: Campaign
) -> None:
    """The raw row of an unparsed record has the file's own headers: the mapping leads back."""
    batch = make_import_batch(
        campaign=campaign,
        source="csv",
        file_sha256="a" * 64,
        column_mapping={"Firma": "name", "Webseite": "website"},
        total_count=1,
    )
    make_import_row(
        batch=batch,
        row_number=1,
        outcome="failed",
        error_code="row_malformed",
        error_message="bad row",
        name="",
        raw_data={"Firma": "Muster GmbH", "Webseite": "muster.example"},
    )
    row = report_table(api.get(report_url(batch.pk)))[1]
    assert row[1:3] == ["Muster GmbH", "muster.example"]


def test_the_error_report_uploads_cleanly_again_after_the_fixes(
    api: APIClient, campaign: Campaign, mixed: str
) -> None:
    text = report_text(api.get(report_url(mixed)))
    fixed = text.replace("'@SUM(1+1)", "gamma.example.com")
    # The fixed file is a normal import: the preview reads the four company columns and the
    # importer ignores the report's own columns.
    upload = SimpleUploadedFile("fixed.csv", fixed.encode("utf-8"), content_type="text/csv")
    preview = api.post(
        f"/api/v1/campaigns/{campaign.pk}/csv-import/preview/", {"file": upload}, format="multipart"
    )
    assert preview.status_code == 200, preview.content
    assert preview.json()["mapping"] == {
        "name": "name",
        "website": "website",
        "profile_url": "profile_url",
        "country": "country",
    }
    batch_id = start_import(api, campaign, fixed.encode("utf-8"), name="fixed.csv")
    body = api.get(detail_url(batch_id)).json()
    assert body["total_count"] == 3
    assert body["created_count"] == 1  # Gamma Ltd now has a website
    assert body["failed_count"] == 2  # the two rows that still have none


# ------------------------------------------------------------------ streaming and efficiency


def make_failed_rows(batch: ImportBatch, count: int) -> None:
    for n in range(1, count + 1):
        make_import_row(
            batch=batch,
            row_number=n,
            outcome="failed",
            error_code="website_invalid",
            error_message="bad",
            raw_data={"name": f"Co {n}", "website": "bad site"},
        )
    ImportBatch.objects.filter(pk=batch.pk).update(total_count=count, failed_count=count)


def test_a_large_report_streams_in_chunks_with_constant_queries(
    api: APIClient, campaign: Campaign
) -> None:
    count = 1300
    batch = make_import_batch(campaign=campaign, total_count=1)
    make_failed_rows(batch, count)
    response = api.get(report_url(batch.pk))
    assert response.streaming is True
    chunks = 0
    lines = 0
    with CaptureQueriesContext(connection) as queries:
        for chunk in response.streaming_content:
            chunks += 1
            lines += chunk.count(b"\r\n")
    assert lines == count + 1
    assert chunks >= 3  # the header, then 500 rows at a time
    assert len(queries) <= 2  # one cursor, never one query per row


def test_the_report_generator_does_not_hold_all_rows(campaign: Campaign) -> None:
    batch = make_import_batch(campaign=campaign, total_count=1)
    make_failed_rows(batch, 30)
    stream = error_report.iter_error_report(batch)
    assert isinstance(stream, Iterator)
    header = next(stream)
    assert header.startswith(b"\xef\xbb\xbfrow_number")
    assert sum(len(c.splitlines()) for c in stream) == 30


def count_queries(run: Callable[[], Any]) -> int:
    with CaptureQueriesContext(connection) as queries:
        run()
    return len(queries)


def test_rows_query_count_does_not_grow_with_the_page(api: APIClient, campaign: Campaign) -> None:
    batch = make_import_batch(campaign=campaign, total_count=1)
    api.get(rows_url(batch.pk))  # warm up
    base = count_queries(lambda: api.get(rows_url(batch.pk)))
    for n in range(1, 26):
        company = make_company(campaign=campaign, name=f"Co {n}")
        other = make_company(campaign=campaign, name=f"Other {n}")
        make_import_row(
            batch=batch,
            row_number=n,
            outcome="created",
            company=company,
            candidate=other,
            match_strength="weak",
            matched_on="name_country",
            name=f"Co {n}",
        )
    ImportBatch.objects.filter(pk=batch.pk).update(total_count=25, created_count=25)
    full = count_queries(lambda: api.get(rows_url(batch.pk)))
    assert api.get(rows_url(batch.pk)).json()["count"] == 25
    assert full <= base + 1  # the page query and the count: same shape for 1 or 25 rows


def test_history_query_count_does_not_grow_with_the_page(
    api: APIClient, campaign: Campaign
) -> None:
    make_import_batch(campaign=campaign)
    api.get(history_url(campaign))
    base = count_queries(lambda: api.get(history_url(campaign)))
    for _ in range(20):
        make_import_batch(campaign=campaign)
    assert count_queries(lambda: api.get(history_url(campaign))) == base


def test_detail_query_count_is_fixed(api: APIClient, campaign: Campaign, mixed: str) -> None:
    api.get(detail_url(mixed))
    few = count_queries(lambda: api.get(detail_url(mixed)))
    batch = make_import_batch(campaign=campaign, total_count=1)
    make_failed_rows(batch, 40)
    many = count_queries(lambda: api.get(detail_url(batch.pk)))
    assert many == few
