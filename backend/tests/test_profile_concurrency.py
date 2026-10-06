"""Concurrent profile edits get distinct, consecutive version numbers (issue #39).

Needs real row locks and committed data, so it only runs on PostgreSQL. It lives in its own file
because the module-level `django_db` mark of the other test files would wrap it in a transaction
the worker threads cannot see.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections, connection

from apps.campaigns.models import Campaign
from apps.campaigns.services import create_profile_version
from tests.factories import make_campaign


@pytest.mark.skipif(connection.vendor != "postgresql", reason="needs row locks (PostgreSQL)")
@pytest.mark.django_db(transaction=True)
def test_concurrent_edits_get_distinct_consecutive_versions():
    campaign = make_campaign()
    workers = 8
    barrier = threading.Barrier(workers)

    def edit(n: int) -> int:
        try:
            barrier.wait(timeout=10)
            current = Campaign.objects.get(pk=campaign.pk)
            return create_profile_version(current, {"offer": f"Offer {n}"}).version
        finally:
            close_old_connections()
            connection.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        versions = list(pool.map(edit, range(workers)))

    assert sorted(versions) == list(range(2, workers + 2))
    fresh = Campaign.objects.get(pk=campaign.pk)
    assert fresh.current_profile.version == workers + 1
    assert fresh.profiles.count() == workers + 1
