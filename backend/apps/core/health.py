"""Liveness and readiness probes.

``/healthz`` answers "is the process up?" and touches nothing else, so a database outage never
makes an orchestrator kill a healthy web process. ``/readyz`` answers "can this deployment do
real work?" with a per-check breakdown and HTTP 503 when any check fails.

Checks (each cheap, none can raise out of the view):

* ``database``: ``SELECT 1`` under a short statement timeout on PostgreSQL.
* ``migrations``: no unapplied migrations for the migration graph on disk.
* ``worker``: a Django-Q2 cluster published a fresh heartbeat. Every running cluster refreshes a
  signed ``Stat`` record (about twice a second) in the cache named by ``Q_CLUSTER["cache"]``;
  that record expires after 3 seconds, so its presence plus a recent ``timestamp`` means the
  cluster is alive. The cache must be shared between processes (see ``config.settings.base``).

Responses carry fixed strings only: exception messages, SQL, hostnames and settings are logged
(with the request ID) but never returned.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django_q.conf import Conf
from django_q.status import Stat
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .schema import error_responses

logger = logging.getLogger(__name__)

OK = "ok"
FAIL = "fail"
SKIPPED = "skipped"

DB_STATEMENT_TIMEOUT_MS = 2000
DEFAULT_WORKER_MAX_AGE_SECONDS = 30
MAX_PENDING_LISTED = 10


@dataclass(frozen=True)
class CheckResult:
    status: str
    detail: str | None = None
    extra: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status}
        if self.detail:
            data["detail"] = self.detail
        if self.extra:
            data.update(self.extra)
        return data


def check_database() -> CheckResult:
    """The default database accepts a trivial query within the statement timeout."""
    try:
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                # Bounds the query only; connection time is bounded by ``connect_timeout``.
                cursor.execute(f"SET statement_timeout = {int(DB_STATEMENT_TIMEOUT_MS)}")
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.warning("Readiness: database check failed", exc_info=True)
        # A broken connection may stay cached on the thread; drop it so the next probe retries.
        connection.close()
        return CheckResult(FAIL, "database unreachable")
    return CheckResult(OK)


def check_migrations() -> CheckResult:
    """Every migration on disk has been applied."""
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:
        logger.warning("Readiness: migration check failed", exc_info=True)
        return CheckResult(FAIL, "could not read migration state")
    if plan:
        pending = [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
        return CheckResult(
            FAIL,
            "pending migrations",
            {"pending": pending[:MAX_PENDING_LISTED], "pending_count": len(pending)},
        )
    return CheckResult(OK)


def fetch_cluster_stats() -> list[Any]:
    """Heartbeat records of all running Django-Q2 clusters sharing our cluster name."""
    return list(Stat.get_all())


def worker_max_age() -> timedelta:
    seconds = getattr(settings, "HEALTH_WORKER_MAX_AGE_SECONDS", DEFAULT_WORKER_MAX_AGE_SECONDS)
    return timedelta(seconds=seconds)


def check_worker(fetch: Callable[[], list[Any]] | None = None) -> CheckResult:
    """At least one Django-Q2 cluster has a recent, non-stopped heartbeat."""
    try:
        stats = (fetch or fetch_cluster_stats)()
    except Exception:
        logger.warning("Readiness: worker heartbeat lookup failed", exc_info=True)
        return CheckResult(FAIL, "could not read worker heartbeat")

    now = timezone.now()
    max_age = worker_max_age()
    fresh = [
        s
        for s in stats
        if str(getattr(s, "status", "")) != str(Conf.STOPPED) and now - s.timestamp <= max_age
    ]
    if not fresh:
        return CheckResult(FAIL, "no recent worker heartbeat")
    newest = max(s.timestamp for s in fresh)
    return CheckResult(
        OK,
        extra={
            "clusters": len(fresh),
            "heartbeat_age_seconds": round(max(0.0, (now - newest).total_seconds()), 1),
        },
    )


def run_readiness_checks() -> tuple[bool, dict[str, CheckResult]]:
    """Run all checks. Checks that need the database are skipped when it is unreachable."""
    results: dict[str, CheckResult] = {"database": check_database()}
    if results["database"].ok:
        results["migrations"] = check_migrations()
        results["worker"] = check_worker()
    else:
        skipped = CheckResult(SKIPPED, "database unavailable")
        results["migrations"] = skipped
        results["worker"] = skipped
    return all(r.ok for r in results.values()), results


class HealthSerializer(serializers.Serializer[dict[str, Any]]):
    """Body of `/healthz` (documentation only)."""

    status = serializers.ChoiceField(choices=[OK])


class CheckSerializer(serializers.Serializer[dict[str, Any]]):
    """One readiness check; the extra fields depend on the check (documentation only)."""

    status = serializers.ChoiceField(choices=[OK, FAIL, SKIPPED])
    detail = serializers.CharField(required=False, help_text="Fixed explanation of a failure.")
    pending = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="migrations: up to 10 unapplied migrations.",
    )
    pending_count = serializers.IntegerField(required=False, help_text="migrations: total pending.")
    clusters = serializers.IntegerField(required=False, help_text="worker: live clusters.")
    heartbeat_age_seconds = serializers.FloatField(
        required=False, help_text="worker: age of the newest heartbeat."
    )


class ReadinessSerializer(serializers.Serializer[dict[str, Any]]):
    """Body of `/readyz`: 200 when `status` is `ok`, 503 when `unavailable`."""

    status = serializers.ChoiceField(choices=[OK, "unavailable"])
    checks = serializers.DictField(
        child=CheckSerializer(), help_text="Keyed by check: database, migrations, worker."
    )


class _ProbeView(APIView):
    """Public, unthrottled and unauthenticated; probes must work with no credentials."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = ()

    def finalize_response(
        self, request: Request, response: Response, *args: Any, **kwargs: Any
    ) -> Response:
        response = super().finalize_response(request, response, *args, **kwargs)
        add_never_cache_headers(response)  # probe results must never be cached by a proxy
        return response


class HealthzView(_ProbeView):
    """Liveness: the process is serving requests. Touches no dependency."""

    @extend_schema(
        tags=["health"],
        operation_id="health_live",
        summary="Liveness probe",
        responses={200: HealthSerializer, **error_responses(500)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response({"status": OK})


class ReadyzView(_ProbeView):
    """Readiness: 200 when every check passes, otherwise 503 with the same JSON body."""

    @extend_schema(
        tags=["health"],
        operation_id="health_ready",
        summary="Readiness probe",
        responses={
            200: ReadinessSerializer,
            503: OpenApiResponse(
                response=ReadinessSerializer, description="At least one check failed."
            ),
            **error_responses(500),
        },
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        ready, results = run_readiness_checks()
        body = {
            "status": OK if ready else "unavailable",
            "checks": {name: result.as_dict() for name, result in results.items()},
        }
        code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(body, status=code)
