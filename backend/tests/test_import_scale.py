"""Import fixtures and performance budgets (issue #70).

Q_CLUSTER is in sync mode under the test settings, so each upload runs the whole Django-Q2 task
inline. Running the same task against a real ``qcluster`` worker is a manual check (see
``docs/imports.md``): CI has no worker process. Retry and resume after a forced failure are
covered by ``test_csv_import`` (a worker restart, a crash midway, attempts running out).

Two budgets are failing checks, not hopes. 500 rows run in every test run. The 5,000-row file
(the most one upload may hold) takes minutes, so it runs only when ``IMPORT_PERF_5000=1`` is set
(``IMPORT_PERF_5000=1 pytest tests/test_import_scale.py -k 5000 -s``) and runs the task directly,
because Django-Q2 would stop a task in sync mode after ``Q_TASK_TIMEOUT`` (300 s).
"""

from __future__ import annotations

import os
import time
import tracemalloc
from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.core.models import Job
from apps.imports import csv_import, uploads
from apps.imports.models import ImportBatch, ImportBatchStatus, ImportRow
from tests.factories import make_campaign, make_client, make_company
from tests.import_fixtures import (
    EXISTING_DOMAIN,
    arabic_csv,
    clean_csv,
    latin1_csv,
    messy_csv,
)
from tests.test_csv_import import as_user, upload, user_with

pytestmark = [pytest.mark.api, pytest.mark.django_db]

#: Seconds for 500 rows, run inline on the test database (about 15 s on a developer laptop).
BUDGET_SECONDS_500 = 60.0
#: Seconds for 5,000 rows run directly (411 s measured on a developer laptop, whose time per row
#: grows with the file) and the peak extra Python memory (MiB) while importing them (about 1 MiB
#: measured: the file is streamed, not loaded whole).
BUDGET_SECONDS_5000 = 900.0
BUDGET_MEMORY_MIB_5000 = 100.0


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Any:
    storage = uploads.upload_storage()
    yield
    try:
        for name in storage.listdir("")[1]:
            storage.delete(name)
    except FileNotFoundError:
        pass


@pytest.fixture
def campaign() -> Campaign:
    return make_campaign(client=make_client())


@pytest.fixture
def api(campaign: Campaign) -> APIClient:
    return as_user(user_with("manager", campaign.client))


def run_import(api: APIClient, campaign: Campaign, content: bytes) -> ImportBatch:
    response = upload(api, campaign, content)
    assert response.status_code == 202, response.content
    batch = ImportBatch.objects.get(pk=response.json()["batch"]["id"])
    batch.refresh_from_db()
    return batch


def counters(batch: ImportBatch) -> dict[str, int]:
    return {
        "total": batch.total_count,
        "created": batch.created_count,
        "duplicate": batch.duplicate_count,
        "skipped": batch.skipped_count,
        "failed": batch.failed_count,
        "restored": batch.restored_count,
    }


# ------------------------------------------------------------------ clean files


@pytest.mark.parametrize("rows", [50, 100, 500])
def test_a_clean_file_imports_every_row(api: APIClient, campaign: Campaign, rows: int) -> None:
    batch = run_import(api, campaign, clean_csv(rows))
    assert batch.status == ImportBatchStatus.COMPLETED
    assert counters(batch) == {
        "total": rows,
        "created": rows,
        "duplicate": 0,
        "skipped": 0,
        "failed": 0,
        "restored": 0,
    }
    assert Company.objects.filter(campaign=campaign).count() == rows
    assert ImportRow.objects.filter(batch=batch).count() == rows
    assert uploads.upload_storage().listdir("")[1] == []  # the file is gone once it is done


def test_importing_the_same_clean_file_again_creates_nothing_new(
    api: APIClient, campaign: Campaign
) -> None:
    run_import(api, campaign, clean_csv(100))
    again = run_import(api, campaign, clean_csv(100))
    assert counters(again)["created"] == 0
    assert counters(again)["duplicate"] == 100
    assert Company.objects.filter(campaign=campaign).count() == 100


# ------------------------------------------------------------------ messy and non-ASCII files


def test_the_counters_of_a_messy_file_are_exact(api: APIClient, campaign: Campaign) -> None:
    make_company(campaign=campaign, name="Existing Co", website=f"https://{EXISTING_DOMAIN}")
    messy = messy_csv()
    batch = run_import(api, campaign, messy.content)
    assert counters(batch) == {
        "total": messy.total,
        "created": messy.created,
        "duplicate": messy.duplicate,
        "skipped": messy.skipped,
        "failed": messy.failed,
        "restored": 0,
    }
    assert batch.status == ImportBatchStatus.PARTIAL  # some rows failed, none stopped the file
    assert batch.processed_count == messy.total
    # Every counted row has a stored result, and nothing was created twice.
    assert ImportRow.objects.filter(batch=batch).count() == messy.total
    assert Company.objects.filter(campaign=campaign, name="Acme Logistics").count() == 1
    assert Company.objects.filter(campaign=campaign).count() == 1 + messy.created


def test_a_messy_file_gives_a_downloadable_error_report(api: APIClient, campaign: Campaign) -> None:
    make_company(campaign=campaign, name="Existing Co", website=f"https://{EXISTING_DOMAIN}")
    batch = run_import(api, campaign, messy_csv().content)
    report = api.get(f"/api/v1/imports/{batch.pk}/errors.csv")
    assert report.status_code == 200
    lines = b"".join(cast(Any, report).streaming_content).decode().strip().splitlines()
    assert len(lines) == 1 + messy_csv().failed  # the header and one line per failed row


def test_arabic_only_names_survive_the_import(api: APIClient, campaign: Campaign) -> None:
    batch = run_import(api, campaign, arabic_csv(40))
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.created_count == 40
    names = set(Company.objects.filter(campaign=campaign).values_list("name", flat=True))
    assert len(names) == 40
    assert all(any("؀" <= ch <= "ۿ" for ch in name) for name in names)
    assert "شركة النور للتجارة" in " ".join(names) or any("شركة" in n for n in names)


def test_a_legacy_encoding_is_decoded_not_garbled(api: APIClient, campaign: Campaign) -> None:
    batch = run_import(api, campaign, latin1_csv())
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.created_count == 3
    names = set(Company.objects.filter(campaign=campaign).values_list("name", flat=True))
    assert {"Société Générale Logistique", "Müller & Söhne GmbH", "Peña Hermanos S.A."} == names


# ------------------------------------------------------------------ the budgets


def test_500_rows_import_within_the_time_budget(api: APIClient, campaign: Campaign) -> None:
    started = time.perf_counter()
    batch = run_import(api, campaign, clean_csv(500))
    elapsed = time.perf_counter() - started
    print(f"METRIC 500 rows: {elapsed:.1f}s")
    assert batch.created_count == 500
    assert elapsed < BUDGET_SECONDS_500, (
        f"500 rows took {elapsed:.1f}s (budget {BUDGET_SECONDS_500}s)"
    )


@pytest.mark.skipif(
    os.environ.get("IMPORT_PERF_5000") != "1",
    reason="takes minutes; run with IMPORT_PERF_5000=1 (see the module docstring)",
)
def test_5000_rows_stay_within_the_time_and_memory_budget(
    api: APIClient, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Accept the upload but leave the task for the direct call below.
    monkeypatch.setattr(csv_import, "async_task", lambda *args, **kwargs: "task-1")
    response = upload(api, campaign, clean_csv(5000))
    assert response.status_code == 202, response.content
    batch = ImportBatch.objects.get(pk=response.json()["batch"]["id"])
    job = Job.objects.get(pk=response.json()["job_id"])

    tracemalloc.start()
    started = time.perf_counter()
    try:
        csv_import.run_csv_import(job, str(batch.pk))
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    peak_mib = peak / (1024 * 1024)
    print(f"METRIC 5000 rows: {elapsed:.0f}s, peak {peak_mib:.0f} MiB")
    batch.refresh_from_db()
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.created_count == 5000
    assert Company.objects.filter(campaign=campaign).count() == 5000
    assert elapsed < BUDGET_SECONDS_5000, f"5,000 rows took {elapsed:.0f}s"
    assert peak_mib < BUDGET_MEMORY_MIB_5000, f"peak memory {peak_mib:.0f} MiB"
