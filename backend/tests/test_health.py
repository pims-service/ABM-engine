"""Tests for /healthz and /readyz (apps/core/health.py)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import OperationalError
from django.utils import timezone
from django_q.brokers import get_broker
from django_q.conf import Conf
from django_q.signing import SignedPackage
from django_q.status import Stat, Status
from rest_framework.test import APIClient

from apps.core import health
from apps.core.health import (
    CheckResult,
    check_database,
    check_migrations,
    check_worker,
    run_readiness_checks,
)

SECRET_MARKERS = ("test-only-insecure-key", "Traceback", "sqlite", "password", "SELECT")


def fresh_stat(age: float = 0.5, status: str | None = None) -> Status:
    stat = Status(pid=1234, cluster_id="cluster-a")
    stat.timestamp = timezone.now() - timedelta(seconds=age)
    stat.status = status or Conf.IDLE
    return stat


@pytest.fixture
def worker_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "fetch_cluster_stats", lambda: [fresh_stat()])


@pytest.fixture
def clean_stats_cache() -> Iterator[None]:
    broker = get_broker()
    broker.cache.clear()
    yield
    broker.cache.clear()


# ----------------------------------------------------------------- /healthz
def test_healthz_is_ok_and_unauthenticated(api_client: APIClient) -> None:
    response = api_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-store" in response["Cache-Control"]
    assert response["X-Request-ID"]


def test_healthz_does_not_touch_the_database(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("healthz must not use the database")

    monkeypatch.setattr(health, "connection", SimpleNamespace(cursor=boom, vendor="x"))
    monkeypatch.setattr(health, "fetch_cluster_stats", boom)
    # No `db` fixture either: any query would fail pytest-django's database blocker.
    assert api_client.get("/healthz").status_code == 200


def test_probes_echo_inbound_request_id(api_client: APIClient) -> None:
    response = api_client.get("/healthz", HTTP_X_REQUEST_ID="probe-123")
    assert response["X-Request-ID"] == "probe-123"


def test_probes_reject_other_methods_with_error_envelope(api_client: APIClient) -> None:
    response = api_client.post("/healthz", {}, format="json")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_probes_allow_head(api_client: APIClient) -> None:
    assert api_client.head("/healthz").status_code == 200


def test_probes_are_not_throttled(
    api_client: APIClient, settings: Any, worker_up: None, db: None
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
        "DEFAULT_THROTTLE_RATES": {"anon": "1/hour", "user": "1/hour"},
    }
    from rest_framework.settings import api_settings

    api_settings.reload()
    try:
        for _ in range(5):
            assert api_client.get("/healthz").status_code == 200
            assert api_client.get("/readyz").status_code == 200
    finally:
        api_settings.reload()


# ----------------------------------------------------------------- /readyz
@pytest.mark.django_db
def test_readyz_ok_when_everything_is_up(api_client: APIClient, worker_up: None) -> None:
    response = api_client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == {"status": "ok"}
    assert body["checks"]["migrations"] == {"status": "ok"}
    assert body["checks"]["worker"]["status"] == "ok"
    assert body["checks"]["worker"]["clusters"] == 1
    assert response["X-Request-ID"]


@pytest.mark.django_db
def test_readyz_503_when_database_is_down(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*args: Any, **kwargs: Any) -> None:
        raise OperationalError('connection to server at "10.0.0.5" failed: password=hunter2')

    monkeypatch.setattr(connection, "cursor", down)
    monkeypatch.setattr(connection, "close", lambda: None)
    response = api_client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == {"status": "fail", "detail": "database unreachable"}
    assert body["checks"]["migrations"]["status"] == "skipped"
    assert body["checks"]["worker"]["status"] == "skipped"
    raw = response.content.decode()
    assert "hunter2" not in raw
    assert "10.0.0.5" not in raw


@pytest.mark.django_db
def test_readyz_503_with_pending_migration(
    api_client: APIClient, worker_up: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration = SimpleNamespace(app_label="core", name="0099_new_thing")
    monkeypatch.setattr(
        MigrationExecutor,
        "migration_plan",
        lambda self, targets, clean_start=False: [(migration, False)],
    )
    response = api_client.get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"] == {
        "status": "fail",
        "detail": "pending migrations",
        "pending": ["core.0099_new_thing"],
        "pending_count": 1,
    }
    assert checks["worker"]["status"] == "ok"


@pytest.mark.django_db
def test_readyz_503_with_real_unapplied_migration(api_client: APIClient, worker_up: None) -> None:
    """Un-apply a real migration record so the genuine executor reports it pending."""
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.recorder import MigrationRecorder

    # The newest core migration is the one a plan reports when un-applied (not an inner one).
    leaf = MigrationLoader(connection).graph.leaf_nodes("core")[0][1]

    MigrationRecorder(connection).record_unapplied("core", leaf)
    try:
        response = api_client.get("/readyz")
    finally:
        MigrationRecorder(connection).record_applied("core", leaf)
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"]["pending"] == [f"core.{leaf}"]


@pytest.mark.django_db
def test_readyz_503_when_no_worker_heartbeat(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "fetch_cluster_stats", lambda: [])
    response = api_client.get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"]["status"] == "ok"
    assert checks["worker"] == {"status": "fail", "detail": "no recent worker heartbeat"}


@pytest.mark.django_db
def test_readyz_503_with_stale_worker_heartbeat(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "fetch_cluster_stats", lambda: [fresh_stat(age=120)])
    response = api_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["detail"] == "no recent worker heartbeat"


@pytest.mark.django_db
def test_readyz_never_leaks_internals(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode() -> list[Any]:
        raise RuntimeError("secret-token-abc Traceback /srv/app/settings.py")

    monkeypatch.setattr(health, "fetch_cluster_stats", explode)
    response = api_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["detail"] == "could not read worker heartbeat"
    raw = response.content.decode()
    for marker in (*SECRET_MARKERS, "secret-token-abc", "/srv/app"):
        assert marker.lower() not in raw.lower()


# ----------------------------------------------------------------- unit checks
@pytest.mark.django_db
def test_check_database_ok() -> None:
    assert check_database() == CheckResult("ok")


@pytest.mark.django_db
def test_check_migrations_ok_and_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    assert check_migrations().ok

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(MigrationExecutor, "migration_plan", broken)
    result = check_migrations()
    assert result.status == "fail"
    assert result.detail == "could not read migration state"


def test_check_worker_ignores_stopped_cluster() -> None:
    result = check_worker(lambda: [fresh_stat(status=Conf.STOPPED)])
    assert not result.ok


def test_check_worker_respects_configured_max_age(settings: Any) -> None:
    stats = [fresh_stat(age=10)]
    assert check_worker(lambda: stats).ok
    settings.HEALTH_WORKER_MAX_AGE_SECONDS = 5
    assert not check_worker(lambda: stats).ok


def test_check_worker_uses_newest_of_several_clusters() -> None:
    result = check_worker(lambda: [fresh_stat(age=20), fresh_stat(age=1), fresh_stat(age=500)])
    assert result.ok
    assert result.extra is not None
    assert result.extra["clusters"] == 2
    assert result.extra["heartbeat_age_seconds"] == pytest.approx(1, abs=1)


@pytest.mark.django_db
def test_worker_heartbeat_round_trip_through_shared_cache(clean_stats_cache: None) -> None:
    """A heartbeat saved the way a qcluster saves it is visible to the web process."""
    assert not check_worker().ok  # nothing published yet

    stat = fresh_stat()
    broker = get_broker()
    key = Stat.get_key(stat.cluster_id)
    broker.set_stat(key, SignedPackage.dumps(stat, True), 3)

    assert check_worker().ok
    assert json.dumps(run_readiness_checks()[1]["worker"].as_dict())


@pytest.mark.django_db
def test_run_readiness_checks_all_ok(worker_up: None) -> None:
    ready, results = run_readiness_checks()
    assert ready
    assert set(results) == {"database", "migrations", "worker"}


# ----------------------------------------------------------------- worker_healthcheck command
@pytest.mark.django_db
def test_worker_healthcheck_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setattr(health, "fetch_cluster_stats", lambda: [])
    with pytest.raises(CommandError, match="no recent worker heartbeat"):
        call_command("worker_healthcheck")

    monkeypatch.setattr(health, "fetch_cluster_stats", lambda: [fresh_stat()])
    out = StringIO()
    call_command("worker_healthcheck", stdout=out)
    assert "worker ok" in out.getvalue()
