"""Enqueue a smoke task on the Django-Q2 worker and optionally wait for its outcome."""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.campaigns.models import Client
from apps.core.jobs import enqueue
from apps.core.models import JOB_TERMINAL, JobStatus
from apps.core.tasks import flaky, ping

SMOKE_CLIENT_NAME = "Smoke test (system)"


class Command(BaseCommand):
    help = (
        "Enqueue a smoke task: 'ping' (round trip), 'flaky' (fails twice, then succeeds after "
        "backoff) or 'fail' (always fails, ends in the failed state). Jobs need a client: pass "
        "--client NAME, or the client 'Smoke test (system)' is created and used. A worker must "
        "be running (manage.py qcluster) unless Q_CLUSTER sync mode is on."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("task", nargs="?", default="ping", choices=["ping", "flaky", "fail"])
        parser.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="Poll status.")
        parser.add_argument(
            "--client", default=SMOKE_CLIENT_NAME, help="Name of an existing client."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        task = options["task"]
        client = self._client(options["client"])
        if task == "ping":
            job = enqueue(ping, client=client, message="pong")
        elif task == "flaky":
            job = enqueue(flaky, client=client, succeed_on_attempt=3)
        else:
            job = enqueue(flaky, client=client, succeed_on_attempt=99)
        self.stdout.write(f"Enqueued {job.type} job {job.pk}")

        deadline = time.monotonic() + options["wait"]
        last = ""
        while True:
            job.refresh_from_db()
            line = (
                f"status={job.status} attempts={job.attempts} "
                f"done={job.done_count}/{job.total_count} failed={job.failed_count}"
            )
            if line != last:
                self.stdout.write(
                    line + (f" error={job.error_summary}" if job.error_summary else "")
                )
                last = line
            if job.status in JOB_TERMINAL or time.monotonic() >= deadline:
                break
            time.sleep(0.5)

        if options["wait"] and job.status not in JOB_TERMINAL:
            raise CommandError(f"Job {job.pk} did not finish within {options['wait']}s")
        if job.status == JobStatus.FAILED and task != "fail":
            raise CommandError(f"Job {job.pk} failed: {job.error_summary}")

    def _client(self, name: str) -> Client:
        if name == SMOKE_CLIENT_NAME:
            client, _ = Client.objects.get_or_create(name=name, archived_at__isnull=True)
            return client
        try:
            return Client.objects.active().get(name=name)
        except Client.DoesNotExist:
            raise CommandError(f"No active client named {name!r}.") from None
