"""Exit 0 when a Django-Q2 worker heartbeat is fresh, 1 otherwise (for container healthchecks)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.health import check_worker


class Command(BaseCommand):
    help = "Exit non-zero unless a Django-Q2 cluster published a recent heartbeat."

    def handle(self, *args: Any, **options: Any) -> None:
        result = check_worker()
        if not result.ok:
            raise CommandError(result.detail or "worker unhealthy")
        self.stdout.write("worker ok")
