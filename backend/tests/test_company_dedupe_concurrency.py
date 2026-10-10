"""Concurrent submissions of one company yield exactly one company (issue #58).

Needs real transactions and committed data, so it only runs on PostgreSQL. It lives in its own
file because the module-level ``django_db`` mark of the other test files would wrap each test in
a transaction the worker threads cannot see (same reason as ``test_profile_concurrency.py``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import close_old_connections, connection

from apps.companies.models import Company
from apps.companies.services import CompanyResult, create_company
from apps.imports import services as import_services
from apps.imports.models import ImportRow, ImportRowOutcome
from tests.factories import make_campaign
from tests.factories_imports import make_import_batch

pytestmark = [
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs real concurrency"),
    pytest.mark.django_db(transaction=True),
]

WORKERS = 8


def _race(work: Callable[[int], Any]) -> list[Any]:
    barrier = threading.Barrier(WORKERS)

    def run(n: int) -> Any:
        try:
            barrier.wait(timeout=10)
            return work(n)
        finally:
            close_old_connections()
            connection.close()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(run, range(WORKERS)))


def _one_winner(results: list[CompanyResult]) -> None:
    assert sum(r.created for r in results) == 1
    assert sum(r.duplicate for r in results) == WORKERS - 1
    assert len({r.company.pk for r in results}) == 1


def test_concurrent_creates_of_one_domain_give_one_company():
    campaign = make_campaign()
    results = _race(
        lambda n: create_company(campaign, f"Acme {n}", f"https://www.acme.com/page{n}")
    )
    _one_winner(results)
    assert Company.objects.filter(campaign=campaign, domain="acme.com").count() == 1


def test_concurrent_creates_of_one_profile_give_one_company():
    campaign = make_campaign()
    urls = [
        "https://www.linkedin.com/company/acme",
        "https://uk.linkedin.com/company/ACME/about/",
        "linkedin.com/company/acme?trk=x",
        "https://m.linkedin.com/company/acme/posts",
    ]
    results = _race(
        lambda n: create_company(campaign, f"Acme {n}", profile_url=urls[n % len(urls)])
    )
    _one_winner(results)
    assert Company.objects.filter(campaign=campaign, profile_key="linkedin:acme").count() == 1


def test_a_winner_is_never_edited_by_the_losers():
    campaign = make_campaign()
    results = _race(lambda n: create_company(campaign, f"Acme {n}", "acme.com", country="SA"))
    winner = Company.objects.get(campaign=campaign)
    created = next(r for r in results if r.created)
    assert winner.name == created.company.name
    assert winner.possible_duplicate_of is None


def test_concurrent_creates_in_different_campaigns_do_not_collide():
    campaigns = [make_campaign() for _ in range(WORKERS)]
    results = _race(lambda n: create_company(campaigns[n], "Acme", "acme.com"))
    assert all(r.created for r in results)
    assert Company.objects.filter(domain="acme.com").count() == WORKERS


def test_concurrent_import_rows_of_one_domain_record_one_created_and_the_rest_duplicates():
    campaign = make_campaign()
    batch = make_import_batch(campaign=campaign, source="manual", total_count=WORKERS)

    def work(n: int) -> ImportRow:
        return import_services.process_row(
            batch, n + 1, {"name": f"Acme {n}", "website": "https://acme.com"}
        )

    rows = _race(work)
    assert Company.objects.filter(campaign=campaign).count() == 1
    outcomes = sorted(r.outcome for r in rows)
    assert outcomes == [ImportRowOutcome.CREATED] + [ImportRowOutcome.DUPLICATE] * (WORKERS - 1)
    for row in rows:
        if row.outcome == ImportRowOutcome.DUPLICATE:
            assert row.match_strength == "strong"
            assert row.matched_on == "domain"
    batch.refresh_from_db()
    assert (batch.created_count, batch.duplicate_count) == (1, WORKERS - 1)
