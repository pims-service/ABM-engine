# Usage, budgets and provider cache

How the engine records what provider calls cost, stops runaway spend, and avoids paying twice for
the same answer, without keeping data a provider's licence forbids. Issues #79 and #80. Rules for
adapters are in [adapter-requirements.md](adapter-requirements.md) (sections 8 and 9); the
framework itself is in [framework.md](framework.md).

## Which function to call

| Need | Call | Notes |
| --- | --- | --- |
| Run an operation, log it, enforce the budget | `usage.tracked_execute(connection, adapter, op, *args, job=None, campaign=None, user=None, **kwargs)` | Replaces `execute`. Same errors, plus `BudgetExceeded`. |
| The same plus the response cache | `caching.cached_execute(connection, adapter, op, *args, force_refresh=False, job=..., **kwargs)` | Same signature plus `force_refresh`. |
| Write a row by hand | `usage.record_call(connection, operation=..., status=...)` | Never raises. |
| Budget decision before a call | `usage.check_budget(connection)` | `allow`, `warn` or `deny`. |
| Set a budget | `usage.set_budget(connection, user, credit_limit=..., cost_limit=..., currency=..., warn_at_percent=80, hard_stop=True)` | Audited. |
| Declare what may be cached | `cache_policy.register_cache_policy(category, provider, CachePolicy(...))` | Default: nothing. |
| Hand over the raw answer | `payloads.attach_raw_payload(payload)` | From inside an adapter operation. |
| Totals | `reporting.summarize`, `totals`, `run_totals`, `cost_per_accepted_account` | |

`connection.provider` must equal `adapter.key` and the categories must match (a `ValueError`
otherwise): a connection and its adapter are one pair.

## Usage log (`ProviderCall`)

One append-only row per call: client, connection, optional campaign and job, category, provider,
operation, `status` (`ok` or `error`), `error_code` (a `ProviderError.code`, or `budget_exceeded`),
`latency_ms`, `credits`, `cost` + `currency`, tokens and model for LLMs, `cache_hit`, `request_id`,
`created_at`. Rules:

- exactly one row per call, including failures, cache hits (credits and cost `0`) and calls refused
  by a budget;
- no request body, no response body, no secret: the table has no such column, and only the numbers
  and the request id of `Usage` and `Provenance` are copied;
- a failure to write the row never fails the call: it is logged, swallowed and counted
  (`usage.logging_failure_count()`), so monitoring can alert on it;
- credits are the provider's own unit and `None` when the provider does not say. Money is never
  summed across currencies: every aggregate also splits by `currency`.

## Budgets

`UsageBudget` belongs to one connection and limits one UTC calendar month: `credit_limit` and/or
`cost_limit` (+ `currency`), `warn_at_percent` (default 80) and `hard_stop` (default true).

- `used >= warn_at_percent` of a limit: `warn` (the call runs, a warning is logged);
- `used >= limit` with `hard_stop`: `deny`: `tracked_execute` raises `BudgetExceeded` (a
  `ProviderQuotaExceeded`, code `budget_exceeded`, not retryable) before any provider request;
- `used >= limit` without `hard_stop`: only `warn`;
- comparisons are exact `Decimal` arithmetic; cost counts rows in the budget currency and rows with
  no currency;
- the check reads the log, it does not reserve: concurrent calls may overshoot a limit by what they
  cost. Hard stops are a guard rail, not an accounting boundary.

## API

All client scoped (see [permissions.md](../permissions.md)): manager and admin read, admin writes.

- `GET /api/v1/integrations/usage/`: the call log, filters `client`, `connection`, `campaign`, `job`,
  `category`, `provider`, `operation`, `status`, `cache_hit`, `date_from`, `date_to` (UTC days).
- `GET /api/v1/integrations/usage/summary/?group_by=provider,day`: groups `provider`, `category`,
  `operation`, `connection`, `campaign`, `job`, `status`, `model`, `day`, `month`; returns
  `totals` (per currency) and `results` (at most 1000 groups).
- `GET|PUT /api/v1/integrations/connections/{id}/budget/`: the budget and this month's usage and
  `state`; PUT replaces the budget (omitted limits mean no cap).

"Cost per accepted account" is a placeholder: `reporting.cost_per_accepted_account(cost, accepted)`
takes the accepted count from the review flow (M7/M8) and returns `None` when unknown.

## Cache policy

A provider's licence may forbid storing or reusing its data, so the default is **no caching and no
raw retention**. An adapter (or the operator) opts in after reading the licence:

```python
register_cache_policy(
    "company_data",
    "acme",
    CachePolicy(
        allow_cache=True,
        default_ttl_seconds=14 * DAY,
        operation_ttl_seconds={"list_job_postings": DAY},
        allow_store_raw=True,
        retention_days=30,
        note="ACME terms 4.2",
    ),
)
```

or, without code, `INTEGRATION_CACHE_POLICIES = {"company_data/acme": {"allow_cache": True, ...}}`
in settings (wins over the code registration). The policy is looked up by `(category, provider key)`,
so the merged adapter API is unchanged.

A connection can only **narrow** the provider policy, through its non-secret `config["cache"]`:
`allow_cache: false`, `allow_store_raw: false`, `ttl_seconds`, `retention_days`,
`max_payload_bytes` (each is a cap). It can never widen it. A malformed value fails closed (nothing
cached or kept). Writes (`push_*`, `export_*`, `trigger`, `put_object`, ...) are never cached.

## Response cache (`CachedResponse`)

- Key: SHA-256 of client, connection, provider, operation and the canonical request (dataclasses,
  enums, dates and mappings are normalized; a request that cannot be keyed faithfully, such as an
  LLM schema class, is not cached). Lookups also filter by client, so one client never reads another
  client's entry.
- A hit makes no provider request, costs nothing, writes a `ProviderCall` with `cache_hit=True`, and
  returns the result with `provenance.cache_hit=True` and the original `retrieved_at`.
- `force_refresh=True` calls the provider again (new `ProviderCall`, new raw payload) and replaces
  the cache entry. The cache is not history; the usage log and raw payloads are.
- Only successful answers are cached. The payload is the encoded result (only types of
  `framework.normalized`, never pickle), credential-like keys masked, at most
  `max_payload_bytes` (default 256 KiB, hard cap 1 MiB); a larger answer is simply not cached.
- **Not encrypted at the application level.** The payload is the normalized result, which can hold
  personal data (people, emails). The mitigations are the opt-in policy, short TTLs, the purge, the
  API never serving it and the admin never showing it. Disk and backup encryption is the database's
  job. If a licence needs more, leave `allow_cache` off.

## Raw payloads (`RawPayload`)

An adapter calls `attach_raw_payload(payload)` inside an operation. If the connection's effective
policy has `allow_store_raw`, `tracked_execute` stores it (secrets masked in keys and text, at most
`max_payload_bytes`) for that call with `expires_at = now + retention_days`, and sets
`provenance.raw_ref = "rawpayload:<id>"`. Otherwise the payload is dropped and never persisted.
Reach it through `call.raw_payload`. The API only says `has_raw_payload`.

## Retention: `purge_provider_cache`

```
python manage.py purge_provider_cache [--dry-run] [--client ID] [--provider KEY] [--all]
```

Deletes cache entries and raw payloads that are expired, or that the connection's current policy no
longer allows (caching or raw storage switched off, an operation no longer cacheable, a shortened
TTL or retention). `--all` with `--client` and/or `--provider` removes everything in scope whatever
the policy says: for an erasure request or a provider opt-out. `ProviderCall` rows are never
deleted. Schedule it daily with Django-Q2:

```python
Schedule.objects.update_or_create(
    name="purge-provider-cache",
    defaults={"func": "apps.integrations.caching.purge_task", "schedule_type": Schedule.DAILY},
)
```

Purging by vendor record id (a provider's opt-out API) is not built yet; use `--provider` scope.
