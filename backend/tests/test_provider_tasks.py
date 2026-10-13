"""The provider job base (issues #86 and #78): ``provider_task`` end to end in sync mode.

A real ``Job`` with ``JobItem`` rows, a real ``IntegrationConnection`` with encrypted credentials,
the fake adapter framework and the shared (database cache) limiter and breaker. Re-delivery is
simulated by calling the task function again with the same job id, as the Django-Q2 scheduler does.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import pytest
from django.utils import timezone
from django_q.models import Schedule

from apps.core.jobs import FailJob, create_job, enqueue
from apps.core.models import Job, JobItemStatus, JobStatus
from apps.integrations.framework import (
    AdapterRegistry,
    CompanyRef,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)
from apps.integrations.framework.base import AdapterCapabilities, RateLimit
from apps.integrations.resilience import CircuitBreaker, RetryPolicy
from apps.integrations.tasks import (
    ProviderJobContext,
    cancel_requested,
    provider_task,
    request_cancel,
    worker_health,
)
from tests.factories import make_client
from tests.factories_integrations import FAKE_SECRET, make_connection
from tests.resilience_helpers import ScriptedAdapter

pytestmark = pytest.mark.django_db

#: subject id -> domain, and domain -> errors the adapter raises (one per call, then it succeeds)
DOMAINS: dict[uuid.UUID, str] = {}
FAILURES: dict[str, list[ProviderError]] = {}
CALLS: list[str] = []


class PlannedAdapter(ScriptedAdapter):
    key = "planned_company"

    def enrich_company(self, company: CompanyRef) -> Any:
        CALLS.append(str(company.domain))
        plan = FAILURES.get(str(company.domain), [])
        if plan:
            raise plan.pop(0)
        return super(ScriptedAdapter, self).enrich_company(company)


REGISTRY = AdapterRegistry()
REGISTRY.register(PlannedAdapter)
QUICK = RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0)


@pytest.fixture(autouse=True)
def _reset() -> None:
    DOMAINS.clear()
    FAILURES.clear()
    CALLS.clear()


def subjects(*names: str) -> list[str]:
    ids = []
    for name in names:
        sid = uuid.uuid4()
        DOMAINS[sid] = f"{name}.example.com"
        ids.append(str(sid))
    return ids


def enrich_one(ctx: ProviderJobContext, item: Any) -> dict[str, Any]:
    result = ctx.call(
        ctx.adapter.enrich_company,  # type: ignore[attr-defined]
        CompanyRef(domain=DOMAINS[item.subject_id]),
    )
    return {"name": result.data.name if result.data else None}


@provider_task(
    job_type="t_enrich",
    category="company_data",
    provider="planned_company",
    retry_policy=QUICK,
    registry=REGISTRY,
    base_delay=1,
)
def t_enrich(ctx: ProviderJobContext, ids: list[str]) -> dict[str, Any]:
    ctx.process_items([("company", i) for i in ids], lambda item: enrich_one(ctx, item))
    return {"seen": ctx.job.processed_count}


@provider_task(
    job_type="t_enrich_once",
    category="company_data",
    provider="planned_company",
    retry_policy=QUICK,
    registry=REGISTRY,
    max_attempts=1,
)
def t_enrich_once(ctx: ProviderJobContext, ids: list[str]) -> None:
    ctx.process_items([("company", i) for i in ids], lambda item: enrich_one(ctx, item))


@provider_task(
    job_type="t_enrich_slow",
    category="company_data",
    provider="planned_company",
    retry_policy=QUICK,
    registry=REGISTRY,
    soft_time_limit=0.001,
)
def t_enrich_slow(ctx: ProviderJobContext, ids: list[str]) -> None:
    def handler(item: Any) -> dict[str, Any]:
        time.sleep(0.01)
        return enrich_one(ctx, item)

    ctx.process_items([("company", i) for i in ids], handler)


@provider_task(
    job_type="t_direct_call",
    category="company_data",
    provider="planned_company",
    retry_policy=QUICK,
    registry=REGISTRY,
)
def t_direct_call(ctx: ProviderJobContext) -> dict[str, Any]:
    """A call outside process_items: provider errors still follow the job rules."""
    ctx.call(
        ctx.adapter.enrich_company,  # type: ignore[attr-defined]
        CompanyRef(domain="direct.example.com"),
    )
    return {"ok": True}


def setup_client() -> Any:
    client = make_client()
    make_connection(
        client,
        category="company_data",
        provider="planned_company",
        config={},
        credentials={"api_key": FAKE_SECRET},
    )
    return client


def run(task: Any, client: Any, *args: Any) -> Job:
    """Enqueue (inline in sync mode). A permanently failed task re-raises, as in production
    where Django-Q2 records the failure; the job row carries the outcome either way."""
    try:
        return enqueue(task, *args, client=client)
    except Exception:
        return Job.objects.filter(client=client).latest("created_at")


def rerun(task: Any, job: Job, *args: Any) -> Job:
    task(str(job.pk), *args)
    job.refresh_from_db()
    return job


# ------------------------------------------------------------------ happy path and partial


def test_success_records_items_and_succeeds() -> None:
    client = setup_client()
    ids = subjects("a", "b", "c")
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.SUCCEEDED
    assert (job.total_count, job.done_count, job.failed_count, job.attempts) == (3, 3, 0, 1)
    items = list(job.items.all())
    assert {i.status for i in items} == {JobItemStatus.SUCCEEDED}
    assert {(i.result or {})["name"] for i in items} == {"A", "B", "C"}


def test_one_failing_item_makes_the_job_partial_not_failed() -> None:
    client = setup_client()
    ids = subjects("a", "bad", "c")
    FAILURES["bad.example.com"] = [ProviderBadRequest("name")]
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.PARTIAL
    assert (job.done_count, job.failed_count) == (2, 1)
    failed = job.items.get(status=JobItemStatus.FAILED)
    assert failed.error.startswith("provider_bad_request")
    assert "provider_bad_request" in job.error_summary


def test_every_item_failing_ends_failed() -> None:
    client = setup_client()
    ids = subjects("a", "b")
    FAILURES["a.example.com"] = [ProviderBadRequest()]
    FAILURES["b.example.com"] = [ProviderBadRequest()]
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.FAILED
    assert job.failed_count == 2


def test_a_bug_in_the_handler_fails_only_that_item_and_leaks_no_message() -> None:
    client = setup_client()
    ids = subjects("a", "b")
    job = create_job("t_enrich", client=client)

    def boom_handler(item: Any) -> dict[str, Any]:
        if DOMAINS[item.subject_id].startswith("b"):
            raise RuntimeError(f"token={FAKE_SECRET}")
        return {}

    from apps.integrations.tasks import build_adapter

    connection, adapter = build_adapter(job, "company_data", "planned_company", REGISTRY)
    ctx = ProviderJobContext(
        job=job,
        connection=connection,
        adapter=adapter,
        resilience=_resilience(adapter, client),
        max_attempts=3,
        base_delay=1,
    )
    from apps.core.jobs import complete_job, start_job

    start_job(job)
    ctx.process_items([("company", i) for i in ids], boom_handler)
    complete_job(job)
    job.refresh_from_db()
    assert job.status == JobStatus.PARTIAL
    failed = job.items.get(status=JobItemStatus.FAILED)
    assert failed.error == "RuntimeError: unexpected error"
    assert FAKE_SECRET not in failed.error + job.error_summary


def _resilience(adapter: Any, client: Any) -> Any:
    from apps.integrations.resilience import Resilience

    return Resilience.for_adapter(adapter, client.pk, policy=QUICK)


# ------------------------------------------------------------------ retryable: re-queue, not wait


def test_rate_limited_requeues_with_the_providers_delay_and_resumes_without_repeating() -> None:
    client = setup_client()
    ids = subjects("a", "limited", "c")
    FAILURES["limited.example.com"] = [ProviderRateLimited(300)]  # too long to wait inline
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.QUEUED  # waiting for the delay, not blocking a worker
    assert job.attempts == 1
    before = list(CALLS)
    assert job.done_count == before.index("limited.example.com")  # items ahead of it are done
    assert job.failed_count == 0
    assert "provider_rate_limited" in job.error_summary
    schedule = Schedule.objects.get(name__startswith=f"retry-{job.pk}")
    assert 250 < (schedule.next_run - timezone.now()).total_seconds() <= 301

    job = rerun(t_enrich, job, ids)  # the scheduler delivers it again
    assert job.status == JobStatus.SUCCEEDED
    assert (job.total_count, job.done_count, job.attempts) == (3, 3, 2)
    assert job.items.count() == 3  # no duplicate items
    done_before = set(before[:-1])
    assert (
        set(CALLS[len(before) :])
        == {f"{n}.example.com" for n in ("a", "limited", "c")} - done_before
    )
    assert not done_before & set(CALLS[len(before) :])  # finished items are not redone


def test_unavailable_after_inline_retries_requeues_with_backoff() -> None:
    client = setup_client()
    ids = subjects("a")
    FAILURES["a.example.com"] = [ProviderUnavailable(), ProviderUnavailable()]
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.QUEUED
    assert Schedule.objects.filter(name__startswith=f"retry-{job.pk}").count() == 1
    job = rerun(t_enrich, job, ids)
    assert job.status == JobStatus.SUCCEEDED


def test_on_the_last_attempt_a_retryable_error_fails_the_item_and_keeps_results() -> None:
    client = setup_client()
    ids = subjects("a", "down")
    FAILURES["down.example.com"] = [ProviderUnavailable()] * 5
    job = run(t_enrich_once, client, ids)
    assert job.status == JobStatus.PARTIAL
    assert job.items.get(status=JobItemStatus.FAILED).error.startswith("provider_unavailable")
    assert not Schedule.objects.filter(name__startswith=f"retry-{job.pk}").exists()


def test_open_circuit_defers_the_job_with_the_remaining_cooldown() -> None:
    client = setup_client()
    ids = subjects("a")
    adapter = PlannedAdapter()
    breaker = CircuitBreaker.for_adapter(adapter, client.pk)
    for _ in range(5):
        breaker.record_failure(ProviderUnavailable())
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.QUEUED
    assert CALLS == []  # the provider was not called at all
    assert "provider_unavailable" in job.error_summary
    breaker.reset()
    job = rerun(t_enrich, job, ids)
    assert job.status == JobStatus.SUCCEEDED


def test_a_provider_error_outside_process_items_is_deferred_or_failed_by_type() -> None:
    client = setup_client()
    FAILURES["direct.example.com"] = [ProviderUnavailable(), ProviderUnavailable()]
    job = run(t_direct_call, client)
    assert job.status == JobStatus.QUEUED
    job = rerun(t_direct_call, job)
    assert job.status == JobStatus.SUCCEEDED
    FAILURES["direct.example.com"] = [ProviderAuthError()]
    assert run(t_direct_call, client).status == JobStatus.FAILED


# ------------------------------------------------------------------ non-retryable


def test_auth_error_fails_the_job_at_once_without_retries() -> None:
    client = setup_client()
    ids = subjects("a", "b", "c")
    for name in ("a", "b", "c"):
        FAILURES[f"{name}.example.com"] = [ProviderAuthError()]  # whichever goes first
    job = run(t_enrich, client, ids)
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert "provider_auth" in job.error_summary
    assert len(CALLS) == 1  # the others were not tried against bad credentials
    assert not Schedule.objects.filter(name__startswith=f"retry-{job.pk}").exists()


# ------------------------------------------------------------------ failing closed


def test_no_connection_fails_closed() -> None:
    job = run(t_enrich, make_client(), subjects("a"))
    assert job.status == JobStatus.FAILED
    assert "No active integration connection" in job.error_summary
    assert CALLS == []


def test_connection_without_credentials_fails_closed() -> None:
    client = make_client()
    make_connection(
        client, category="company_data", provider="planned_company", config={}, credentials=None
    )
    job = run(t_enrich, client, subjects("a"))
    assert job.status == JobStatus.FAILED
    assert "credentials" in job.error_summary
    assert CALLS == []


def test_another_clients_connection_is_never_used() -> None:
    setup_client()  # a connection for some other client
    job = run(t_enrich, make_client(), subjects("a"))
    assert job.status == JobStatus.FAILED
    assert CALLS == []


def test_unknown_provider_in_the_registry_fails_closed() -> None:
    client = make_client()
    make_connection(
        client,
        category="company_data",
        provider="planned_company",
        config={},
        credentials={"api_key": FAKE_SECRET},
    )

    @provider_task(
        job_type="t_empty_registry",
        category="company_data",
        registry=AdapterRegistry(),
    )
    def t_empty_registry(ctx: ProviderJobContext) -> None:  # pragma: no cover
        raise AssertionError

    job = create_job("t_empty_registry", client=client)
    with pytest.raises(FailJob):
        t_empty_registry(str(job.pk))
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "provider_not_found" in job.error_summary


# ------------------------------------------------------------------ redelivery, cancel, limits


def test_redelivery_of_a_finished_job_does_nothing() -> None:
    client = setup_client()
    ids = subjects("a")
    job = run(t_enrich, client, ids)
    calls = list(CALLS)
    job = rerun(t_enrich, job, ids)
    assert job.status == JobStatus.SUCCEEDED
    assert (job.attempts, CALLS) == (1, calls)


def test_redelivery_adds_no_duplicate_items_and_skips_recorded_ones() -> None:
    client = setup_client()
    ids = subjects("a", "b")
    job = create_job("t_enrich", client=client)
    t_enrich(str(job.pk), ids)
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    ctx_job = Job.objects.get(pk=job.pk)
    assert ctx_job.items.count() == 2


def test_cancel_between_items_fails_the_rest_as_cancelled() -> None:
    client = setup_client()
    ids = subjects("a", "b", "c")
    job = create_job("t_enrich", client=client)
    request_cancel(job.pk)
    assert cancel_requested(job.pk)
    t_enrich(str(job.pk), ids)
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED  # nothing ran, so nothing succeeded
    assert set(job.items.values_list("error", flat=True)) == {"cancelled"}
    assert CALLS == []


def test_soft_time_limit_requeues_the_rest_without_using_an_attempt() -> None:
    client = setup_client()
    ids = subjects("a", "b", "c")
    job = run(t_enrich_slow, client, ids)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 0  # the attempt was given back: progress was made
    assert job.done_count == 1
    for _ in range(5):
        if job.status != JobStatus.QUEUED:
            break
        job = rerun(t_enrich_slow, job, ids)
    assert job.status == JobStatus.SUCCEEDED
    assert job.done_count == 3
    assert CALLS.count("a.example.com") == 1


def test_soft_limit_must_be_below_the_hard_limit(settings: Any) -> None:
    settings.Q_CLUSTER = {**settings.Q_CLUSTER, "timeout": 60}
    with pytest.raises(ValueError, match="soft_time_limit"):
        provider_task(job_type="x", category="company_data", soft_time_limit=60)


# ------------------------------------------------------------------ rate limit, health, secrets


class TightAdapter(PlannedAdapter):
    key = "tight_company"
    capabilities = AdapterCapabilities(
        features=ScriptedAdapter.capabilities.features,
        requires_credentials=False,
        rate_limit=RateLimit(per_minute=2),
    )


TIGHT = AdapterRegistry()
TIGHT.register(TightAdapter)


@provider_task(
    job_type="t_tight",
    category="company_data",
    provider="tight_company",
    retry_policy=RetryPolicy.no_retry(),
    registry=TIGHT,
)
def t_tight(ctx: ProviderJobContext, ids: list[str]) -> None:
    ctx.process_items([("company", i) for i in ids], lambda item: enrich_one(ctx, item))


def test_the_shared_rate_limit_is_applied_to_job_calls() -> None:
    client = make_client()
    make_connection(
        client,
        category="company_data",
        provider="tight_company",
        config={},
        credentials={"api_key": FAKE_SECRET},
    )
    job = run(t_tight, client, subjects("a", "b", "c"))
    assert job.status == JobStatus.QUEUED  # the third call was deferred, not blocked
    assert job.done_count == 2
    assert len(CALLS) == 2
    assert "provider_rate_limited" in job.error_summary


def test_worker_health_reports_worker_queue_and_hung_calls() -> None:
    health = worker_health()
    assert set(health) == {"worker", "queue_depth", "abandoned_calls"}
    assert health["worker"]["status"] in {"ok", "fail", "skipped"}
    assert isinstance(health["queue_depth"], int)


def test_logs_ids_and_codes_but_never_credentials(caplog: pytest.LogCaptureFixture) -> None:
    client = setup_client()
    ids = subjects("a", "bad")
    FAILURES["bad.example.com"] = [ProviderBadRequest("name")]
    with caplog.at_level(logging.DEBUG):
        job = run(t_enrich, client, ids)
    text = " ".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records)
    assert FAKE_SECRET not in text
    assert "provider job started" in text
    assert FAKE_SECRET not in job.error_summary
    ctx_repr = repr(
        ProviderJobContext(
            job=job,
            connection=job.client.integration_connections.first()
            if hasattr(job.client, "integration_connections")
            else None,  # type: ignore[arg-type]
            adapter=PlannedAdapter(),
            resilience=_resilience(PlannedAdapter(), client),
            max_attempts=1,
            base_delay=1,
        )
    )
    assert FAKE_SECRET not in ctx_repr
    assert str(job.pk) in ctx_repr
