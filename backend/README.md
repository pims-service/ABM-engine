# ABM Engine backend

Django 5.2 + Django REST Framework API. PostgreSQL for data. Background jobs run on Django-Q2
with a Postgres-backed queue (no Redis, no Celery); see "Background jobs" below.
JWT auth (SimpleJWT) is a separate issue.

## Dependency management: uv

We use [uv](https://docs.astral.sh/uv/) (chosen over poetry). Dependencies live in
`pyproject.toml`; the resolved, pinned set is the committed `uv.lock`. Python is pinned to 3.12.

```bash
pip install uv            # or see the uv docs for other installers
uv sync                   # create .venv from uv.lock (includes dev group)
uv add <package>          # add a dependency and update uv.lock
uv lock                   # re-resolve after editing pyproject.toml
```

The Docker image installs with `uv sync --frozen --no-dev`, so it fails if `uv.lock` is out of date.

## Layout

```
config/settings/{base,dev,test,prod}.py   settings, all driven by env vars
config/urls.py                            /admin/, /api/v1/, /healthz, /readyz
apps/{accounts,campaigns,companies,research,integrations,ai,core}   empty apps (AppConfigs registered)
tests/                                    pytest smoke tests, fixtures, factories, examples/
```

The API is versioned by URL namespace: everything lives under `/api/v1/`
(`GET /api/v1/` returns the API name, version and a self link).

## Setup and run

```bash
cd backend
cp .env.example .env      # then fill in the placeholders (SECRET_KEY, DATABASE_URL)
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
curl http://localhost:8000/api/v1/
```

A reachable PostgreSQL is needed for dev (`DATABASE_URL=postgres://USER:PASSWORD@localhost:5432/DBNAME`).
Generate a secret key with:
`python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"`.

## Database, migrations and seed data

PostgreSQL is the real database for dev and prod, configured only through `DATABASE_URL`
(`postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@localhost:5432/YOUR_DB_NAME`). A fresh database
needs one command: `uv run python manage.py migrate`. The `pgcrypto` and `pg_trgm` extensions
are installed by `apps/core/migrations/0001_postgres_extensions.py`, never by hand (the
database role needs permission to `CREATE EXTENSION`; both are "trusted" extensions on
PostgreSQL 13+, so the database owner is enough). The migration is a no-op on SQLite.

### Migration workflow

1. **Change the model**, then generate: `uv run python manage.py makemigrations <app> -n <short_name>`.
   Name migrations after what they do (`add_company_domain_index`), not `auto_2026...`.
   Always pass the app label so unrelated drift is not swept in.
2. **Review the generated file** before committing: check the operations, indexes and defaults,
   and that data-heavy changes (adding a NOT NULL column, rewriting a column) will not lock a big
   table. Split schema and data changes into separate migrations (`RunPython` needs a reverse
   function, or `migrations.RunPython.noop` when reversal is safe to skip).
3. **Apply locally**: `uv run python manage.py migrate`. Inspect SQL with
   `uv run python manage.py sqlmigrate <app> <number>`.
4. **Commit the migration with the model change** in the same PR.

Rules:

- **Never edit or delete a migration that has been merged/applied** anywhere (shared dev, staging,
  prod, a teammate's machine). Fix mistakes with a new migration. Editing is only fine for a
  migration that exists solely on your unmerged branch; in that case reset your local database
  (below) rather than hand-patching it.
- Migrations are immutable history, in line with the keep-history rule in ADR 0007: do not drop
  or rewrite data columns casually; deprecate first, remove in a later release.
- A test (`tests/test_migrations.py`) runs `makemigrations --check`, so CI fails if a model
  change has no migration.
- **Conflicts**: two branches adding a migration to the same app produce two leaf nodes and
  Django refuses to migrate (`Conflicting migrations detected`). After rebasing on main, if
  yours is the later one, re-generate it so it depends on main's latest (delete your unmerged
  migration and run `makemigrations` again), or run `makemigrations --merge` when both are
  independent and already merged. Then re-run the tests.

### Seed data

```bash
export DEV_SUPERUSER_USERNAME=admin DEV_SUPERUSER_EMAIL=YOUR_EMAIL DEV_SUPERUSER_PASSWORD=YOUR_PASSWORD
uv run python manage.py seed_dev_data
```

`seed_dev_data` is idempotent: running it twice changes nothing the second time. It refuses
to run when `DEBUG` is off unless `--force` is passed. Today it only creates the dev superuser
from the `DEV_SUPERUSER_*` variables (username defaults to `admin`; without a password the
step is skipped, so no account with a known password is ever created; an existing user is left
untouched, including its password). There are no domain models yet, so there is no sample
company/campaign data. It is a skeleton to be filled in during M1: add a function to `SEEDERS` in
`apps/core/seeding.py` that is safe to run repeatedly (use `get_or_create` / `update_or_create`).

### Reset the local database

```bash
uv run python manage.py reset_local_db        # asks you to type the database name
uv run python manage.py reset_local_db --yes  # no prompt; add --no-migrate to skip migrating
```

Drops and recreates the database named in `DATABASE_URL`, then migrates. It destroys all data,
so it only runs with `DEBUG` on, against PostgreSQL on a local host (`localhost`, `127.0.0.1`,
`::1`). Follow with `seed_dev_data` to get back a usable dev environment.

### Backup and restore (local dev)

```bash
pg_dump -Fc -h localhost -U YOUR_DB_USER -f abm_dev.dump YOUR_DB_NAME     # backup
createdb -h localhost -U YOUR_DB_USER YOUR_DB_NAME_RESTORED
pg_restore --no-owner -h localhost -U YOUR_DB_USER -d YOUR_DB_NAME_RESTORED abm_dev.dump
```

If PostgreSQL runs in Docker, prefix with `docker exec CONTAINER` (and write the dump to a path
inside the container, then `docker cp` it out). Restore into an empty database; to restore over
the dev database, run `reset_local_db --no-migrate` first. Dumps contain real data, so keep them
out of git.

## Background jobs (Django-Q2)

Slow or bulk work never runs in a web request (ADR 0005). Django-Q2 uses the Django ORM broker, so
the queue and its results live in the same Postgres database; `migrate` creates its tables.
Config is `Q_CLUSTER` in `config/settings/base.py`: acks happen after a task finishes (a worker that
dies mid-task has it redelivered after `Q_TASK_RETRY` seconds), a hard `Q_TASK_TIMEOUT` (300 s)
per task, `Q_WORKERS` processes (2), and the scheduler runs inside the cluster for periodic tasks
(`django_q.tasks.schedule` or the admin "Scheduled tasks").

Run a worker next to the dev server, then enqueue a smoke task:

```bash
uv run python manage.py qcluster                              # in Docker: the `worker` service
uv run python manage.py enqueue_smoke_task ping --wait 30     # round trip
uv run python manage.py enqueue_smoke_task flaky --wait 90    # fails twice, retried with backoff, succeeds
uv run python manage.py enqueue_smoke_task fail --wait 90     # retried, then ends in "failed"
```

### Adding a task

1. In the owning app's `tasks.py`, write a function decorated with `@tracked_job` that takes the
   `BackgroundJob` first, then JSON-only arguments, and returns a JSON dict (or `None`):

   ```python
   from apps.core.jobs import tracked_job, update_progress


   @tracked_job(max_attempts=3, base_delay=5)
   def import_companies(job, campaign_id: int) -> dict[str, int]:
       update_progress(job, 50)
       return {"imported": 120}
   ```

2. Enqueue it from a view, service or command: `job = enqueue(import_companies, campaign_id=7)`.
   `job` is the status record (`queued`, `running`, `retrying`, `succeeded`, `failed`, plus
   `progress`, `attempts`, `error`) to show or poll.
3. Make it idempotent: it can run twice (crash redelivery) or be retried.
4. Test it with the default test settings: `Q_CLUSTER["sync"] = True` runs the task inline, no
   worker needed (`tests/examples/test_task.py`, `tests/test_jobs.py`).

Retries: on an exception the task is rescheduled with exponential backoff (`base_delay`,
2x, 4x, ...) through the Django-Q2 scheduler, status `retrying`; after `max_attempts` runs the job
is `failed` with the error text, and the failure is also visible in the Django admin (Failed tasks).
The scheduler polls roughly every 30 seconds, so backoff delays are rounded up to that granularity.
Django-Q2's own retry is not used for failures (`ack_failures` is on), so attempts are counted once.

JSON only: `enqueue` rejects arguments, and `tracked_job` rejects results, that are not plain JSON
values (so ids, not model instances). Django-Q2 itself has no serializer setting and pickles its
signed envelope internally; the JSON rule is enforced at our boundary, and the broker is our own
database, not an untrusted network.

Scope: `BackgroundJob` (`apps/core/models.py`) is a minimal status record only. The full Job and
AuditLog models are issue #44 and will replace it behind the same `apps.core.jobs` helpers.

## Health and readiness

Two probe endpoints at the site root (not under `/api/v1/`), implemented in `apps/core/health.py`.
They need no authentication, are excluded from throttling, send `Cache-Control: no-store`, carry the
usual `X-Request-ID`, and return fixed strings only (no exception text, SQL, hosts or settings; the
details go to the log with the request ID).

| Endpoint | Meaning | Touches |
| --- | --- | --- |
| `GET /healthz` | Liveness: the process serves requests. Always `200 {"status": "ok"}`. | nothing |
| `GET /readyz` | Readiness: `200` when all checks pass, else `503` with the same JSON shape. | db, migrations, worker |

```json
{"status": "unavailable",
 "checks": {"database": {"status": "ok"},
            "migrations": {"status": "fail", "detail": "pending migrations",
                           "pending": ["core.0004_x"], "pending_count": 1},
            "worker": {"status": "fail", "detail": "no recent worker heartbeat"}}}
```

- `database`: `SELECT 1` with a 2 s statement timeout; connections time out after 3 s
  (`connect_timeout` in `config/settings/base.py`). When it fails, `migrations` and `worker` are
  reported as `skipped`.
- `migrations`: every migration on disk is applied.
- `worker`: a Django-Q2 cluster published a heartbeat (its cluster `Stat`) at most 30 s ago and is
  not stopped. Override the window with the Django setting `HEALTH_WORKER_MAX_AGE_SECONDS`. A
  running cluster refreshes the heartbeat about twice a second and it expires after 3 s, so a stopped
  or crashed worker is detected within seconds. The heartbeat is stored in the `q_stats` cache, a
  database cache (table `q_stats_cache`, created by migration `core.0003`) that the api and the
  worker share; the default per-process cache could not carry it between processes.

Use `/healthz` for liveness/restart decisions (a database outage should not restart the web
process) and `/readyz` for CI, deployment gates and humans. Compose uses `/healthz` for `api` and
`python manage.py worker_healthcheck` (same worker check, exits non-zero when stale) for `worker`.
Behind a TLS-redirecting proxy in prod, both paths are exempt from `SECURE_SSL_REDIRECT`.

```bash
curl -i http://localhost:8000/healthz
curl -i http://localhost:8000/readyz
```

## Tests

```bash
uv run pytest                      # tests + coverage (terminal report, coverage.xml)
uv run pytest tests/examples -k view   # subset
uv run pytest --no-cov             # skip coverage for a quick loop
```

Coverage is configured in `pyproject.toml` (branch coverage over `apps/` and `config/`,
fails under 90%; `coverage.xml` is written for CI to pick up).

Harness (`tests/`): `conftest.py` provides `api_client`, `user` and `auth_client` fixtures
(authentication uses `force_authenticate` until JWT lands); `factories.py` holds factory_boy
factories (`UserFactory`, `make_user()`); the `db` fixture / `@pytest.mark.django_db` gives a
test database. `tests/examples/` has one example per layer to copy from: model, serializer,
view, task. The task example uses Django-Q2 in sync mode (see "Background jobs").

## Code quality

All tool config lives in `pyproject.toml`. Run these from `backend/`:

```bash
uv run ruff check .                # lint (add --fix to apply safe fixes)
uv run ruff format .               # format (use --check to verify only)
uv run mypy .                      # types: strict, django-stubs + djangorestframework-stubs
```

Run all gates in one go: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`.

Makefile targets and container test runs are tracked in other issues. CI runs all of the above; see [docs/ci.md](../docs/ci.md).

## Test settings

Tests use `config.settings.test`, which needs no `.env` or database server: it supplies throwaway
values and uses in-memory SQLite. SQLite is a fallback for the test settings only; dev and prod
require a Postgres `DATABASE_URL`.

To run the suite against real PostgreSQL (this also runs the extension tests, which are skipped
on SQLite), point `DATABASE_URL` at a server whose role can create databases; pytest-django
creates and drops a separate `test_<name>` database:

```bash
docker run -d --name abm-pg -e POSTGRES_PASSWORD=YOUR_DB_PASSWORD -p 55432:5432 postgres:16
DATABASE_URL=postgres://postgres:YOUR_DB_PASSWORD@localhost:55432/abm_dev uv run pytest
```

## Docker

```bash
docker build -t abm-backend backend
docker run --rm -p 8000:8000 \
  -e SECRET_KEY=YOUR_SECRET_KEY -e DATABASE_URL=postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@HOST:5432/YOUR_DB_NAME \
  -e ALLOWED_HOSTS=localhost -e SECURE_SSL_REDIRECT=False abm-backend
```

The image runs gunicorn with `config.settings.prod`. Compose wiring is handled in another issue.

## Logging, request IDs and API errors

Code lives in `apps/core/{logging,middleware,exceptions}.py`; the wiring is the `LOGGING`
setting, `RequestIDMiddleware` (first in `MIDDLEWARE`) and DRF's `EXCEPTION_HANDLER`.

**Structured logs.** Prod/base settings write one JSON object per line to stdout:
`timestamp`, `level`, `logger`, `message`, `request_id` (null outside a request), `job_id` when
set, any `extra={...}` fields, and `exception` for tracebacks. Dev uses a readable line,
`... INFO [request-id] logger: message`. Set `LOG_JSON=true` in dev to preview the JSON format and
`LOG_LEVEL` (default `INFO`) to change verbosity. Add context with `extra=`:
`logger.info("scored", extra={"company_id": 7})`. Background jobs can tag their lines with
`apps.core.logging.set_job_id(...)` / `reset_job_id(token)`.

**Request IDs.** Every request gets an ID: a well-formed inbound `X-Request-ID` (1-128 chars of
`A-Za-z0-9._-`) is reused, anything else is replaced by a generated UUID hex. It is returned in the
`X-Request-ID` response header, stamped on every log record emitted during the request (through a
contextvar, so no plumbing), included in error bodies, and available as `request.request_id`.
One access line (`apps.core.access`: method, path, `status_code`, `duration_ms`) is logged per
request, at WARNING for 4xx and ERROR for 5xx.

**Error format.** Every API error has the same body:

```json
{"error": {"code": "validation_error", "message": "Request validation failed.",
           "details": {"name": ["This field is required."]}, "request_id": "5f0c..."}}
```

| `code` | HTTP | `details` |
| --- | --- | --- |
| `validation_error` | 400 | field errors (`{"field": ["msg"]}`) or a list |
| `parse_error` | 400 | null |
| `not_authenticated` / `authentication_failed` | 401, or 403 for session auth | null |
| `permission_denied` | 403 | null |
| `not_found` | 404 (also unknown `/api/` routes) | null |
| `method_not_allowed` | 405 | null |
| `not_acceptable` / `unsupported_media_type` | 406 / 415 | null |
| `throttled` | 429 | `{"retry_after": seconds}` (also the `Retry-After` header) |
| `internal_error` | 500 | null |
| any other `APIException` | its status | null (`code` is the exception's `default_code`) |

`code` is stable and meant for client logic; `message` is for humans and may change. Raise normal
DRF exceptions (`ValidationError`, `NotFound`, custom `APIException` subclasses with a
`default_code`) and the handler does the rest. Unhandled exceptions return a generic message with
`internal_error` and no traceback or exception text; the full traceback is logged (as
`apps.core.exceptions`, with the request ID) so support can look it up from the ID the client
quotes. With `DEBUG=True` the 500 `details` additionally names the exception class, never its
message. Errors raised outside DRF views under `/api/` (unknown route, middleware failures) use
the same envelope through `handler404`/`handler500`; other paths such as `/admin/` keep Django's
pages.

**Secrets are never logged.** A redaction filter runs on the log handler before formatting. It
masks values of `password`, `token`, `secret`, `api_key`, `authorization`, `cookie`,
`session`/`csrf` style keys (in `extra` fields and in `key=value` / `"key": "value"` text),
`Bearer`/`Basic` credentials, JWTs and the password part of URLs such as `postgres://user:pw@host`,
including inside tracebacks. It is a safety net, not a licence: do not log request bodies or
headers wholesale.

## Environment variables

Every variable is listed in [docs/environment.md](../docs/environment.md), the single source of
truth (name, service, required, default, placeholder, notes). `.env.example` has the placeholders.
Values are read via django-environ; a local `.env` is loaded if present and real environment
variables take precedence.

On startup (every settings module except `config.settings.test`) `config/env_validation.py`
checks the environment and refuses to start, naming every missing or invalid variable in one
error. It never prints values. `SECRET_KEY` and `DATABASE_URL` are always required, and
`ALLOWED_HOSTS` is required in production. Logs pass through a redaction filter that masks
password, token and API-key values (`apps/core/logging.py`).

## DRF defaults

JSON renderer only (browsable API added in dev), JSON parser, page-number pagination
(`page_size` query param, max 100), anon/user throttling enabled with configurable rates
(disabled in test), `IsAuthenticated` as the default permission (the API root is public).
