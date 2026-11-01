"""Import hardening (issue #69): per-user throttling of uploads, the cap on batches running at once,
rejection of files that only claim to be CSV, formula-safe exports and logs without file contents.

Cross-client access is covered where each endpoint is tested (``test_import_batch_api``,
``test_provider_import_api`` and ``test_csv_import``).
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.campaigns.models import Campaign, Client
from apps.imports import csv_import, uploads
from apps.imports import services as import_services
from apps.imports.models import ImportBatch, ImportSource
from tests.factories import make_campaign, make_client
from tests.test_csv_import import (
    as_user,
    cancel_url,
    import_url,
    make_csv,
    preview_url,
    upload,
    user_with,
)

pytestmark = [pytest.mark.api, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _fresh_throttle_counters() -> Any:
    cache.clear()
    yield
    cache.clear()


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
def client_and_campaign() -> tuple[Client, Campaign]:
    client = make_client()
    return client, make_campaign(client=client)


@pytest.fixture
def campaign(client_and_campaign: tuple[Client, Campaign]) -> Campaign:
    return client_and_campaign[1]


@pytest.fixture
def api(client_and_campaign: tuple[Client, Campaign]) -> APIClient:
    return as_user(user_with("manager", client_and_campaign[0]))


@pytest.fixture
def stay_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task is not run inline, so each batch stays pending like before a worker picks it up."""
    monkeypatch.setattr(csv_import, "async_task", lambda *args, **kwargs: "task-1")


def stored_names() -> list[str]:
    return sorted(uploads.upload_storage().listdir("")[1])


# ------------------------------------------------------------------ throttling


def test_the_three_import_throttles_have_configurable_default_rates() -> None:
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["csv_upload"] == "60/hour"
    assert rates["provider_search"] == "300/hour"
    assert rates["provider_import"] == "60/hour"


def test_csv_uploads_and_previews_are_throttled_per_user(
    api: APIClient,
    campaign: Campaign,
    client_and_campaign: tuple[Client, Campaign],
    stay_queued: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"csv_upload": "3/hour"})
    codes = [
        upload(api, campaign, make_csv(2, start=1), preview=True).status_code,
        upload(api, campaign, make_csv(2, start=10), preview=True).status_code,
        upload(api, campaign, make_csv(2, start=20)).status_code,
        upload(api, campaign, make_csv(2, start=30)).status_code,
    ]
    assert codes == [200, 200, 202, 429]
    refused = upload(api, campaign, make_csv(2, start=40)).json()["error"]
    assert refused["code"] == "throttled"
    assert "retry_after" in refused["details"]
    # Another user has their own allowance.
    other = as_user(user_with("manager", client_and_campaign[0]))
    assert upload(other, campaign, make_csv(2, start=50)).status_code == 202
    assert ImportBatch.objects.filter(campaign=campaign).count() == 2


def test_a_throttled_upload_creates_nothing(
    api: APIClient,
    campaign: Campaign,
    stay_queued: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"csv_upload": "1/hour"})
    assert upload(api, campaign, make_csv(2)).status_code == 202
    assert upload(api, campaign, make_csv(2, start=10)).status_code == 429
    assert ImportBatch.objects.filter(campaign=campaign).count() == 1
    assert len(stored_names()) == 1


# ------------------------------------------------------------------ the cap on running batches


def test_a_campaign_cannot_have_more_batches_running_than_the_cap(
    api: APIClient,
    campaign: Campaign,
    stay_queued: None,
    settings: Any,
) -> None:
    settings.IMPORT_MAX_ACTIVE_BATCHES = 2
    assert upload(api, campaign, make_csv(2, start=1)).status_code == 202
    assert upload(api, campaign, make_csv(2, start=10)).status_code == 202
    refused = upload(api, campaign, make_csv(2, start=20))
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "too_many_active_imports"
    assert error["details"] == {"active": 2, "limit": 2}
    # Nothing was left behind by the refused upload.
    assert ImportBatch.objects.filter(campaign=campaign).count() == 2
    assert len(stored_names()) == 2


def test_the_cap_lifts_when_a_batch_ends(
    api: APIClient, campaign: Campaign, stay_queued: None, settings: Any
) -> None:
    settings.IMPORT_MAX_ACTIVE_BATCHES = 1
    first = upload(api, campaign, make_csv(2, start=1))
    assert first.status_code == 202
    assert upload(api, campaign, make_csv(2, start=10)).status_code == 429
    assert api.post(cancel_url(first.json()["batch"]["id"])).status_code == 200
    assert upload(api, campaign, make_csv(2, start=10)).status_code == 202


def test_the_cap_is_per_campaign(
    api: APIClient,
    client_and_campaign: tuple[Client, Campaign],
    stay_queued: None,
    settings: Any,
) -> None:
    settings.IMPORT_MAX_ACTIVE_BATCHES = 1
    client, first = client_and_campaign
    second = make_campaign(client=client)
    assert upload(api, first, make_csv(2, start=1)).status_code == 202
    assert upload(api, first, make_csv(2, start=10)).status_code == 429
    assert upload(api, second, make_csv(2, start=10)).status_code == 202


def test_the_cap_never_blocks_adding_one_company_by_hand(
    api: APIClient, campaign: Campaign, stay_queued: None, settings: Any
) -> None:
    settings.IMPORT_MAX_ACTIVE_BATCHES = 1
    assert upload(api, campaign, make_csv(2)).status_code == 202
    added = api.post(
        f"/api/v1/campaigns/{campaign.pk}/companies/",
        {"name": "Zed Logistics", "website": "zed-logistics.example.com", "analyze": False},
        format="json",
    )
    assert added.status_code == 201, added.content


def test_the_cap_covers_provider_batches_too(campaign: Campaign, settings: Any) -> None:
    settings.IMPORT_MAX_ACTIVE_BATCHES = 1
    import_services.create_batch(campaign, ImportSource.PROVIDER, total_count=1)
    with pytest.raises(import_services.TooManyActiveBatchesError) as info:
        import_services.create_batch(campaign, ImportSource.PROVIDER, total_count=1)
    assert (info.value.active, info.value.limit) == (1, 1)
    # A manual entry is not counted and not refused.
    import_services.create_batch(campaign, ImportSource.MANUAL, total_count=1)


def test_a_finished_batch_does_not_count_against_the_cap(
    api: APIClient, campaign: Campaign, settings: Any
) -> None:
    settings.IMPORT_MAX_ACTIVE_BATCHES = 1
    # Sync mode: each import has finished by the time the next one starts.
    for start in (1, 10, 20):
        assert upload(api, campaign, make_csv(2, start=start)).status_code == 202


# ------------------------------------------------------------------ files that only claim to be CSV


@pytest.mark.parametrize(
    ("label", "content", "codes"),
    [
        ("an xlsx workbook (a zip)", b"PK\x03\x04" + b"\x00" * 64, {"unsupported_format"}),
        ("a PDF", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj", {"unsupported_format"}),
        (
            "a Windows executable",
            b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64,
            {"unsupported_format", "binary_file"},
        ),
        ("random bytes with NULs", bytes(range(256)) * 4, {"binary_file", "undecodable"}),
    ],
)
@pytest.mark.parametrize("preview", [True, False])
def test_a_non_csv_file_renamed_dot_csv_is_rejected(
    api: APIClient,
    campaign: Campaign,
    label: str,
    content: bytes,
    codes: set[str],
    preview: bool,
) -> None:
    response = upload(api, campaign, content, name="companies.csv", preview=preview)
    assert response.status_code == 400, label
    assert response.json()["error"]["code"] in codes, label
    assert ImportBatch.objects.filter(campaign=campaign).count() == 0
    assert stored_names() == []


# ------------------------------------------------------------------ what leaves and what is logged


def test_the_error_report_cannot_run_a_formula(api: APIClient, campaign: Campaign) -> None:
    body = (
        b"company_name,website\n"
        b'=HYPERLINK("http://evil.example"),not a url at all\n'
        b"+cmd|' /C calc'!A0,also not a url\n"
        b"@SUM(1+1),still not a url\n"
    )
    response = upload(api, campaign, body)
    batch_id = response.json()["batch"]["id"]
    report = api.get(f"/api/v1/imports/{batch_id}/errors.csv")
    assert report.status_code == 200
    text = b"".join(cast(Any, report).streaming_content).decode()
    for line in text.splitlines()[1:]:
        for cell in line.split(","):
            assert not cell.lstrip('"').startswith(("=", "+", "@")), line
    assert "'=HYPERLINK" in text
    assert report["X-Content-Type-Options"] == "nosniff"


def test_file_contents_are_never_written_to_the_logs(
    api: APIClient, campaign: Campaign, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "ZXQ-MARKER-4471"
    body = f"company_name,website,notes\n{marker} Ltd,{marker.lower()}.example.com,{marker}\n"
    with caplog.at_level(logging.DEBUG):
        assert upload(api, campaign, body.encode(), name=f"{marker}.csv").status_code == 202
        assert upload(api, campaign, body.encode(), preview=True).status_code == 200
    assert marker not in caplog.text
    assert marker.lower() not in caplog.text


def test_control_and_zero_width_characters_are_stripped_from_stored_names(
    api: APIClient, campaign: Campaign
) -> None:
    body = "company_name,website\nAc​me‮ Ltd﻿,acme-clean.example.com\n".encode()
    batch_id = upload(api, campaign, body).json()["batch"]["id"]
    rows = api.get(f"/api/v1/imports/{batch_id}/rows/").json()["results"]
    assert rows[0]["name"] == "Acme Ltd"


def test_import_endpoints_refuse_another_clients_user(
    campaign: Campaign, stay_queued: None
) -> None:
    stranger = as_user(user_with("manager", make_client()))
    assert upload(stranger, campaign, make_csv(2)).status_code == 404
    assert upload(stranger, campaign, make_csv(2), preview=True).status_code == 404
    assert stranger.get(import_url(campaign)).status_code in (404, 405)
    assert stranger.post(preview_url(campaign)).status_code == 404
    assert ImportBatch.objects.filter(campaign=campaign).count() == 0


def test_every_csv_export_uses_the_one_formula_guard() -> None:
    from apps.companies.api import pipeline
    from apps.imports.csvparse import safe_cell
    from apps.research import agreement

    # The approved-accounts export and the AI-vs-you export share one guard with the problem file.
    assert pipeline.safe is safe_cell
    assert cast(Any, agreement).safe is safe_cell
    for hostile in ("=1+1", "+cmd", "-2", "@SUM(A1)", "\t=1", "​=1", "‮@1"):
        assert pipeline.safe(hostile).startswith("'"), repr(hostile)
    assert pipeline.safe("Acme Logistics") == "Acme Logistics"
