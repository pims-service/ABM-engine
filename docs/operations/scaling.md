# Scaling the background work

Analyses, imports, CRM pushes and the email find and verify steps run as background jobs in the Django-Q2 cluster (`qcluster`),
which keeps its queue in Postgres. There is no Redis.

## What limits throughput

An analysis spends nearly all of its time waiting for the company website and the AI provider, so
the limit is the number of workers, then the provider's own rate limits and your monthly AI
budget (Settings, Usage).

| Setting | Default | Meaning |
|---|---|---|
| `Q_WORKERS` | 4 | Worker processes per cluster. Raise it first. |
| `Q_TASK_TIMEOUT` | 300 | Hard limit for one task, in seconds. |
| `Q_TASK_RETRY` | 360 | Must be larger than the timeout. |
| `JOB_STALE_RUNNING_SECONDS` | 1800 | A running job with no update for this long is failed. |
| `JOB_STALE_QUEUED_SECONDS` | 86400 | A queued job older than this is failed. |

For a big batch, run more than one `qcluster` process (or container) against the same database;
each takes tasks without clashing.

## When a worker dies

A killed or restarted worker used to leave its job open for ever. Now the job is closed as
failed after `JOB_STALE_RUNNING_SECONDS` the next time anyone opens the dashboard summary, starts
an analysis or runs a bulk analysis, and the company can be analysed again. Nothing is lost:
earlier research stays.

## The dashboard

The summary counts come from one query and the list is paginated, so the page stays quick with
thousands of companies. Bulk analysis queues at most 500 companies per request.
