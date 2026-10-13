# 0014. Resilience layer and provider jobs on Postgres only

- Status: Accepted
- Date: 2026-10-12

## Context

Bulk runs over hundreds of companies hit vendor rate limits and transient failures (issues #78
and #86). Handling that once, centrally, beats ad hoc retries in every adapter. The stack has no
Redis or Celery ([ADR 0005](0005-postgres-backed-job-queue.md)): the queue is Django-Q2 on
Postgres. Limits must hold across several worker processes. The adapter framework
([ADR 0013](0013-integration-framework.md)) already gives typed errors with `retryable` and
`retry_after`, and `apps/core/jobs.py` gives Job and JobItem with retry and backoff.

## Decision

We will add a pure-Python resilience layer (`apps/integrations/resilience`) and a provider job
base (`apps/integrations/tasks.py`), with no new service, model or migration.

- Retry policy with backoff and jitter, driven only by the `retryable` flag and `retry_after`.
  A wait too long to sleep inline is raised, so the job layer re-queues instead of holding a worker.
- Rate limiter (token buckets) and circuit breaker per (client, adapter), their state in the
  existing `q_stats` database cache, updated under a Postgres advisory lock. All processes share it.
- A hard in-process deadline (daemon thread) so a hung call cannot block a worker forever.
- `provider_task` extends `tracked_job` instead of replacing it: two small control-flow exceptions
  were added to `core.jobs` (`RetryLater` with a delay, `FailJob` without retries), the rest of the
  retry mechanism is reused. Retryable errors re-queue the job; item-specific errors fail only the
  item; credential, configuration and quota errors fail the job.
- Tunable by one optional settings dict, no environment variables.

## Alternatives considered

- **Redis for limits and breaker state**: faster, but a new service to run and secure, against
  ADR 0005. Rejected; the call rate (tens per second at most) is far below what Postgres handles.
- **A dedicated table with row locks**: durable and queryable, but needs models and migrations
  and gives little over a cache whose loss is harmless. Can replace the cache later behind
  `SharedState` if durable counters or reporting are needed.
- **In-process state only**: simplest, but each worker would count separately and the limit would
  not hold across processes. Kept only as the fallback for tests and local memory caches.
- **`tenacity` or `pybreaker`**: they do not know our typed errors and cannot share state across
  processes, so most of the code would be adapters around them.
- **A separate job model (`JobRun`)**: `Job` and `JobItem` already cover status, attempts, errors
  and per-subject results.

## Consequences

- Adapters stay thin: they map vendor failures to typed errors and declare their `RateLimit`.
- The cache can be culled or expire: a bucket refills or a circuit closes (the safe direction).
  `q_stats` has `MAX_ENTRIES` raised to avoid culling in practice.
- Each limiter or breaker update takes a short advisory lock and a database round trip.
- The hard deadline abandons a thread rather than killing it; the number of abandoned threads is
  capped and reported by `worker_health()`.
- Not done here: separate queues for LLM and other provider calls in Docker Compose, a manual
  re-queue of failed jobs, and a client-scoped status endpoint. Revisit when load needs them, or
  when strict daily quotas need durable counters.
