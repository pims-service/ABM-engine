# Resilience and provider jobs

Retries, timeouts, rate limiting and a circuit breaker for every provider call, plus the job base
that runs provider calls in the background (issues #78 and #86,
[ADR 0014](../adr/0014-resilience-and-provider-jobs.md)). Pure Python plus Django's cache and
Django-Q2: no Redis, no models, no network, no vendor SDK.

Code: `apps/integrations/resilience/` and `apps/integrations/tasks.py`.

## Calling an adapter

Never call an adapter operation directly from business code. Use:

```python
from apps.integrations.resilience import resilient_execute

result = resilient_execute(adapter, adapter.enrich_company, company_ref, client_id=client.pk)
```

`resilient_execute` wraps the framework's `execute()` (contract checks, typed errors) and, per
attempt, applies: the circuit breaker, the rate limiter, the hard deadline, then reports the
outcome to the breaker. Around the attempts sits the retry policy. Whatever escapes is a
`ProviderError` with `code`, `retryable` and `retry_after`.

`client_id` builds the limiter and breaker for the pair (client, adapter) from the adapter's
`capabilities.rate_limit` and the settings. Keyword overrides: `policy=`, `limiter=`, `breaker=`,
`timeouts=`, `sleep=`, `clock=`, `rng=` (these names are reserved and not passed to the operation).
For an operation that is not safe to repeat, pass `policy=RetryPolicy.no_retry()`.

| Piece | What it does |
| --- | --- |
| `RetryPolicy` | Max attempts, base delay, factor, max delay, jitter. Retries only a `ProviderError` with `retryable` (never auth, bad request, not found, quota, ...). `Retry-After` (`error.retry_after`) wins over the computed backoff and is not jittered; one longer than `max_retry_after` is not slept inline: it is raised so a job can re-queue. |
| `TimeoutPolicy` | `connect`, `read` and a hard `deadline`, as data. Hand `connect`/`read` to the HTTP client; `scaled(3)` for slow operations. `User-Agent`: `resilience.config.user_agent()`. |
| `run_with_deadline` | Hard stop for in-process calls: a hung call raises `ProviderTimeout` after the deadline. Limits: the thread cannot be killed (it is abandoned, capped per process), side effects may still happen, and the call must not use the ORM. A timeout in the HTTP client is still the real protection. |
| `RateLimiter` | Token buckets per window (`per_second`, `per_minute`, `per_day`) per (client, category, adapter key), shared across processes. `try_acquire()` never waits; `acquire(timeout)` waits at most `min(timeout, rate_limit_max_block)` then raises `ProviderRateLimited(retry_after)`. |
| `CircuitBreaker` | closed, open, half-open per (client, adapter). Opens after `breaker_failure_threshold` consecutive outage failures within `breaker_window`; after `breaker_cooldown` one probe call is allowed. Open raises `ProviderCircuitOpen` (a `ProviderUnavailable`, code `provider_unavailable`, `retry_after` = remaining cool-down). |

What counts as an outage for the breaker: a retryable error that is not a 429 (unavailable,
timeout). Auth, bad request, not found, quota and rate limiting never open it, and a probe that
gets one of them closes it (the provider answered).

## Settings

One optional dict, `INTEGRATION_RESILIENCE` in `config/settings`; keys and defaults are in
`resilience/config.py` (`retry_*`, `connect_timeout`, `read_timeout`, `hard_deadline`,
`max_abandoned_calls`, `rate_limit_max_block`, `rate_limits` (per adapter key, may only tighten
what the adapter declares), `breaker_*`, `cache_alias`). No environment variables.

## Shared state

Rate limit and circuit state are small dicts in the `q_stats` database cache (Postgres), updated
under a Postgres advisory lock (a plain lock on SQLite and local-memory caches in tests). Every
worker process sees the same budget. Entries expire on their own; if one is lost the bucket refills
or the circuit closes (the safe direction). Call these from outside a long transaction. Do not point
`cache_alias` at a per-process cache in production.

Status for the settings UI (#77): `connection_status(adapter, client_id)` returns the breaker state,
failure counts and remaining tokens (no secrets); a client-scoped endpoint on top of it is not
included yet.

## Provider jobs (`apps/integrations/tasks.py`)

```python
from apps.integrations.tasks import ProviderJobContext, provider_task

@provider_task(job_type="enrich_companies", category="company_data", soft_time_limit=200)
def enrich_companies(ctx: ProviderJobContext, company_ids: list[str]) -> dict[str, Any]:
    def enrich(item: JobItem) -> dict[str, Any]:
        result = ctx.call(ctx.adapter.enrich_company, ref_for(item.subject_id))
        return {"fields": count_fields(result)}

    ctx.process_items([("company", cid) for cid in company_ids], enrich)
    return {"processed": ctx.job.processed_count}

job = enqueue(enrich_companies, client=client, created_by=user, company_ids=ids)
```

`provider_task` is `tracked_job` (`apps/core/jobs.py`) plus:

- **Fail closed**: resolves the client's usable connection, reads credentials with
  `get_credentials`, builds the adapter from the registry (`registry=` to override). Anything
  missing ends the job `failed` with a fixed safe reason, no retries.
- **By error type**: retryable errors re-queue the whole job after `retry_after` (or the backoff)
  using `core.jobs.RetryLater`, so no worker waits; on the last attempt they fail the item instead.
  Auth, not configured and quota errors fail the job at once (`FailJob`). Other provider errors
  and handler bugs fail only that item; the job ends `partial` (or `failed` if nothing worked).
- **Idempotent**: redelivery skips items already succeeded or failed and never duplicates items.
  Make handlers safe to run twice for the one item that was `running`.
- **Time**: `soft_time_limit` (checked between items) ends the run and re-queues the rest without
  using an attempt; the hard limit is `Q_TASK_TIMEOUT`; calls have the `TimeoutPolicy` deadline.
- **Cancel**: `request_cancel(job_id)`; the rest fail as "cancelled" and the job ends from counts.
- **Health**: `worker_health()` gives the worker heartbeat, queue depth and hung call count.

`max_attempts` defaults to 8 for provider tasks because each deferral uses one. Logs carry the job
id plus provider, code and counts; never credentials, arguments or provider text.

## Testing

Use the fakes (`framework/fakes.py`) with injected failures, `FakeClock` style fakes for `clock`
and `sleep` (see `tests/resilience_helpers.py`), and a local cache for the state
(`SharedState(LocMemCache(...))`). Nothing needs the network or sleeping.
