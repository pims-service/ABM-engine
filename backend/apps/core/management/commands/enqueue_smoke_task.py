"""Enqueue a smoke task on the Django-Q2 worker and optionally wait for its outcome."""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.jobs import enqueue
from apps.core.models import JobStatus
from apps.core.tasks import flaky, ping

FINAL_STATES = {JobStatus.SUCCEEDED, JobStatus.FAILED}


class Command(BaseCommand):
    help = (
        "Enqueue a smoke task: 'ping' (round trip), 'flaky' (fails twice, then succeeds after "
        "backoff) or 'fail' (always fails, ends in the failed state). A worker must be running "
        "(manage.py qcluster) unless Q_CLUSTER sync mode is on."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("task", nargs="?", default="ping", choices=["ping", "flaky", "fail"])
        parser.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="Poll status.")

    def handle(self, *args: Any, **options: Any) -> None:
        task = options["task"]
        if task == "ping":
            job = enqueue(ping, message="pong")
        elif task == "flaky":
            job = enqueue(flaky, succeed_on_attempt=3)
        else:
            job = enqueue(flaky, succeed_on_attempt=99, name="fail")
        self.stdout.write(f"Enqueued {job.name} job {job.pk}")

        deadline = time.monotonic() + options["wait"]
        last = ""
        while True:
            job.refresh_from_db()
            line = f"status={job.status} attempts={job.attempts} progress={job.progress}"
            if line != last:
                self.stdout.write(line + (f" error={job.error}" if job.error else ""))
                last = line
            if job.status in FINAL_STATES or time.monotonic() >= deadline:
                break
            time.sleep(0.5)

        if options["wait"] and job.status not in FINAL_STATES:
            raise CommandError(f"Job {job.pk} did not finish within {options['wait']}s")
        if job.status == JobStatus.FAILED and task != "fail":
            raise CommandError(f"Job {job.pk} failed: {job.error}")
