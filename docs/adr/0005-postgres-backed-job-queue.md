# 0005. Background jobs on a Postgres-backed queue (Django-Q2)

- Status: Accepted
- Date: 2026-10-06

## Context

Research, enrichment, verification and AI analysis are slow and call outside
services (Brief §18 asks for queue/workers or n8n). They must not run inside a
web request. We expect modest volume at first: batches of companies per
campaign, not thousands of jobs per second.

We want as few moving parts to run and pay for as possible while the team is
small.

## Decision

We will run background jobs with Django-Q2 using the Django ORM broker, so the
queue lives in the same PostgreSQL database as the rest of the data. We will
not add Redis or Celery.

- Jobs are enqueued from Django code and run by a separate worker process.
- Job functions must be idempotent and safe to retry, because workers can
  crash and tasks can run twice.
- Calls to providers go through the adapters from ADR 0002, with timeouts and
  rate-limit handling.
- Scheduled work (for example periodic refresh) uses Django-Q2's scheduler.

## Alternatives considered

- **Celery with Redis or RabbitMQ**: the standard choice and very capable,
  but it adds a broker to run, monitor and secure, plus more configuration,
  for volume we do not have.
- **Redis-based queues (RQ, Dramatiq)**: simpler than Celery, but still an
  extra service.
- **n8n or another workflow tool**: mentioned in the Brief and fast for
  prototypes, but business logic would live outside the codebase, outside code
  review and tests.
- **Run work inline in the request, or in threads**: no retries, lost work on
  restart, and timeouts for users.
- **A Postgres queue library such as Procrastinate**: a reasonable option.
  Django-Q2 won on Django integration, built-in scheduling and admin
  visibility.

## Consequences

- One fewer service to deploy, back up and pay for. The queue is backed up
  with the database.
- Enqueuing a job can happen in the same transaction as the data change.
- A database-polling queue has limits: higher latency than Redis and extra
  load on Postgres as volume grows. Long-running jobs must not hold open
  transactions.
- Fewer features than Celery (routing, complex workflows).
- Revisit if the queue causes noticeable database load, if job latency matters
  to users, or if we need fan-out workflows beyond what Django-Q2 handles. The
  job functions are plain Python, so moving brokers should be contained.
