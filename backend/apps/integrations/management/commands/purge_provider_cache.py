"""Remove cached provider answers and raw payloads that must not be kept (issue #80).

Run it daily (or schedule ``apps.integrations.caching.purge_task`` with Django-Q2, see
``docs/integrations/usage-and-cache.md``). It deletes:

* cache entries and raw payloads past their ``expires_at``;
* rows the connection's CURRENT ``CachePolicy`` no longer allows (caching or raw storage turned
  off after the data was stored, an operation no longer cacheable, a shortened TTL or retention).

``--all`` (with ``--client`` and/or ``--provider``) removes everything in scope whatever the
policy says: use it for an erasure request or a provider opt-out notice. ``ProviderCall`` rows
(the usage log) are never touched. ``--dry-run`` only reports.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.campaigns.models import Client
from apps.integrations import caching


class Command(BaseCommand):
    help = "Delete expired and no-longer-allowed provider cache entries and raw payloads."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report only; delete nothing.")
        parser.add_argument("--client", help="Limit to one client (id).")
        parser.add_argument("--provider", help="Limit to one provider key.")
        parser.add_argument(
            "--all",
            action="store_true",
            dest="everything",
            help="Delete everything in scope regardless of policy (needs --client or --provider).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        client = None
        if options["client"]:
            try:
                client = Client.objects.get(pk=options["client"])
            except (Client.DoesNotExist, ValueError, ValidationError) as exc:
                raise CommandError("Unknown client id.") from exc
        if options["everything"] and client is None and not options["provider"]:
            raise CommandError("--all needs --client and/or --provider.")
        report = caching.purge_expired(
            client=client,
            provider=options["provider"],
            everything=options["everything"],
            dry_run=options["dry_run"],
        )
        verb = "Would delete" if options["dry_run"] else "Deleted"
        self.stdout.write(
            f"{verb} {report.expired_cache} expired and {report.forbidden_cache} not-allowed "
            f"cache entries, {report.expired_raw} expired and {report.forbidden_raw} not-allowed "
            "raw payloads."
        )
