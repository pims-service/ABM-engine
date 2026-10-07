# ABM Engine backend

Django 5.2 + Django REST Framework API. PostgreSQL for data. Background jobs run on Django-Q2
with a Postgres-backed queue (no Redis, no Celery); see "Background jobs" below.
Authentication is JWT (SimpleJWT) on a custom email-login user; see "Authentication" below.

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
apps/{accounts,campaigns,companies,research,integrations,ai,core}   apps (campaigns has the tenancy models; others are empty AppConfigs)
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
export DEV_SUPERUSER_EMAIL=YOUR_EMAIL DEV_SUPERUSER_PASSWORD=YOUR_PASSWORD
uv run python manage.py seed_dev_data
```

`seed_dev_data` is idempotent: running it twice changes nothing the second time. It refuses
to run when `DEBUG` is off unless `--force` is passed. Today it only creates the dev superuser
from the `DEV_SUPERUSER_*` variables (the email defaults to `admin@example.com`; without a password the
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
   `Job` first, then JSON-only arguments, and returns a JSON dict (or `None`). Its `job_type`
   (default: the function name) is registered in code, so a job's `type` is never free input:

   ```python
   from apps.core.jobs import add_items, complete_item, fail_item, start_item, tracked_job
   from apps.core.models import ITEM_TERMINAL


   @tracked_job(job_type="analyze_companies", max_attempts=3, base_delay=5)
   def analyze_companies(job, company_ids: list[str]) -> dict[str, int]:
       add_items(job, [("company", cid) for cid in company_ids])  # one JobItem each, sets the total
       for item in job.items.exclude(status__in=ITEM_TERMINAL):  # a retry skips finished items
           start_item(item)
           try:
               complete_item(item, analyze(item.subject_id))  # done_count += 1
           except Exception as exc:  # one bad company...
               fail_item(item, f"{type(exc).__name__}: {exc}")  # ...failed_count += 1, job goes on
       return {"analyzed": job.done_count}
   ```

2. Enqueue it from a view, service or command:
   `job = enqueue(analyze_companies, client=client, campaign=campaign, created_by=user, company_ids=ids)`.
   `client` is required (every job carries its tenant; `campaign` and `created_by` are optional);
   every other argument goes to the task. `job` is the status record to show or poll.
3. Make it idempotent: it can run twice (crash redelivery) or be retried.
4. Test it with the default test settings: `Q_CLUSTER["sync"] = True` runs the task inline, no
   worker needed (`tests/examples/test_task.py`, `tests/test_jobs.py`).

Retries: on an exception the task is rescheduled with exponential backoff (`base_delay`,
2x, 4x, ...) through the Django-Q2 scheduler and the job goes back to `queued` with the error in
`error_summary` (`attempts` counts runs); after `max_attempts` runs the job is `failed` with the
error text, and the failure is also visible in the Django admin (Failed tasks). A task delivered
again for a job that already finished is skipped.
The scheduler polls roughly every 30 seconds, so backoff delays are rounded up to that granularity.
Django-Q2's own retry is not used for failures (`ack_failures` is on), so attempts are counted once.

JSON only: `enqueue` rejects arguments, and `tracked_job` rejects results, that are not plain JSON
values (so ids, not model instances). Django-Q2 itself has no serializer setting and pickles its
signed envelope internally; the JSON rule is enforced at our boundary, and the broker is our own
database, not an untrusted network.

### Job, JobItem and the status rules (`apps/core/models.py`, `apps/core/jobs.py`, issue #44)

`Job` replaces the minimal `BackgroundJob` of issue #25 (removed by migration `core.0004`: it had
no client and the dev database holds no production data, so there is no data migration). The
helper API stays: `create_job`, `enqueue`, `tracked_job`, `start_job` (was `mark_running`),
`fail_job` (`mark_failed`), `requeue_job` (`mark_retrying`); `update_progress(percent)` became real
counts (`set_total`, `record_progress`, or items).

- **Fields**: `type`, `client` (required), `campaign` (optional, must be the same client; an
  archived client or campaign takes no new jobs), `status`, `total_count` / `done_count` /
  `failed_count` (`done + failed <= total`, a DB check), `attempts`, `error_summary`,
  `queue_task_id`, `created_by`, `started_at`, `finished_at`, and `progress_percent`.
- **Status machine** (`JOB_TRANSITIONS`): `queued -> running | failed`;
  `running -> succeeded | partial | failed | queued` (back to `queued` = retry waiting);
  `succeeded`, `partial` and `failed` are final. `Job.save` raises `InvalidTransitionError` for
  any other move, so it holds even outside the service functions (a bulk `QuerySet.update` is
  the one way around it; do not use it for `status`). `finished_at` is set exactly when the
  status is final (DB check).
- **JobItem**: one row per subject, so one failed company does not fail the job. It points at
  its subject with `subject_type` + `subject_id` (`"company"` and its UUID) until the Company
  model lands (issue #40), unique per job. Statuses `queued -> running -> succeeded | failed`,
  final once finished (retry a failed subject in a new job). `complete_job` decides the job
  status from the counts: no failures `succeeded`, some `partial`, only failures `failed`; it
  refuses while items are unprocessed, and writes an `error_summary` listing the first errors.
- **Locking**: every count change locks the job row (`SELECT ... FOR UPDATE`) first, then the
  item, in one transaction, so concurrent workers never lose an update (tested on PostgreSQL
  with threads). Stored errors are clipped to 2000 characters and scrubbed of secrets.
- **Worker context**: while a task runs, `get_job_id()` (`apps/core/logging.py`) returns the job
  id, so every log line carries it.
- `enqueue_smoke_task` needs a client: `--client NAME`, or it creates and uses "Smoke test
  (system)". `worker_healthcheck` and `/readyz` do not touch the Job table.
- Archiving a client or campaign (`services.archive_client` / `archive_campaign`) is refused
  while it has `queued` or `running` jobs.

### Audit log (`apps/core/audit.py`, `AuditLog`, issue #44)

`AuditLog` is append-only (the `AppendOnlyModel` guards): `actor` (null = system), `client`
(null for global objects, used for scoping), `action` (`create`, `update`, `archive`, `restore`,
`delete`), `object_type` + `object_id`, `before` / `after` JSON, `request_id` (from the
request-id contextvar), `created_at`.

- **Explicit service calls, not signals.** Every write path in `apps/campaigns/services.py`
  (`create_client`, `update_client`, `archive_client`, `restore_client`, `create_campaign`,
  `update_campaign`, `activate_campaign`, `archive_campaign`, `restore_campaign`,
  `create_profile_version`) writes its audit entry in the same transaction. Signals were rejected:
  they do not know the actor, do not fire for bulk `update()`, and fire for fixtures and
  migrations. The admin actions and edit forms call the services too. A new audited object type
  calls `record_change(action, "object_type", id, actor=..., client=..., before=snapshot(...),
  after=snapshot(...))`.
- **Diffs**: `update`, `archive` and `restore` store only the fields that changed (`before` and
  `after` hold the same keys); no change writes nothing. `create` stores the whole `after`.
  A new CampaignProfile version is audited as a reference (`campaign_id`, `version`,
  `previous_version`, `changed_fields`, `change_note`), not a copy of the rules.
- **Secrets never get in**: keys that look like a password, token, API key, secret, cookie,
  credential, hash, salt or key (`apps.core.audit.is_secret_field`, built on the log redactor)
  become `[REDACTED]` at any depth in both `before` and `after`, and strings are scrubbed for
  `key=value` secrets. The diff is computed first, so a changed secret still shows as changed.
  ClientMembership changes (issue #46) should use the same `record_change` calls.
- The admin shows Job, JobItem and AuditLog read-only. Test factories: `tests/factories_core.py`
  (`make_job`, `make_job_item`, `make_audit_log`).

## Domain model building blocks (`apps/core`)

Design: [data-model.md](../docs/data-model.md) and [ADR 0009](../docs/adr/0009-data-model-conventions.md).
New domain models (issues #40 to #44) compose these abstract bases from `apps/core/base.py`:

| Base | Gives you |
| --- | --- |
| `UUIDModel` | `id` UUID primary key |
| `TimestampedModel` | `created_at`, `updated_at` |
| `BaseModel` | both of the above (use for mutable tables) |
| `ArchivableModel` + `ArchivableQuerySet` | `archived_at`, `is_archived`, `archive()`, `restore()` (idempotent; keeps a `status` column in step via `archived_status` / `restored_status`), `.active()` / `.archived()`. Archiving never cascades. |
| `TenantModel` + `TenantQuerySet` | direct `client` FK (PROTECT). Set `tenant_parent = "<fk name>"` and `save()` copies `client_id` from the parent and raises `TenantMismatchError` on a mismatch or a move to another client. Manager methods `for_user(user)` and `for_client(client_or_id)`. Root tables (like `Campaign`) pass `client` explicitly. A table that is the tenant itself (`Client`) sets `tenant_lookup = "id"` on its queryset. |
| `AppendOnlyModel` + `AppendOnlyQuerySet` | history rows: `save()` on an existing row, `delete()`, and queryset `update` / `bulk_update` / `delete` raise `ImmutableRecordError`. Corrections are new rows. |
| `StringListField` (`apps/core/fields.py`) | ordered list of strings: `text[]` on PostgreSQL, JSON text on SQLite. Defaults to `[]`, never NULL. |

```python
class ThingQuerySet(AppendOnlyQuerySet["Thing"], TenantQuerySet["Thing"]):  # type: ignore[override]
    pass


class Thing(AppendOnlyModel, TenantModel, UUIDModel):
    tenant_parent = "company"  # the parent row's client_id is copied on create
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    objects = ThingQuerySet.as_manager()
```

Rules: always use `PROTECT` (never `CASCADE`), name constraints `<app>_<model>_<what>`, and load
tenant data through `Model.objects.for_user(user)` (views and services) or
`.for_client(client)` (jobs that carry a client), never a bare `Model.objects.all()`.
`for_user` asks `apps/core/tenancy.accessible_client_ids(user)`: global admins (active
superusers) see everything, everyone else sees the clients where they have an active
`ClientMembership`, and anonymous or inactive users see nothing.

### Roles and permissions (issue #46)

`ClientMembership` (`apps/campaigns`) gives a user a role (`admin`, `manager`, `reviewer`,
`viewer`) in one client; `User.is_superuser` is the global admin flag. **Every endpoint serving
client data must subclass `ClientScopedModelViewSet` / `ClientScopedReadOnlyModelViewSet` /
`ClientScopedViewSet` from `apps/core/permissions.py`** and declare `action_levels`
(`Level.READ`, `DECIDE`, `EDIT`, `MANAGE`). That scopes the queryset with `for_user` (404 for
other clients' ids, never 403) and checks the role. Change memberships only with
`apps/campaigns/memberships.py` (`grant_membership`, `change_role`, `revoke_membership`) or the
admin. Matrix and step-by-step guide: [docs/permissions.md](../docs/permissions.md). Worked
example: `tests/permissions_demo.py`.

### Campaigns, versioned profiles (`apps/campaigns`)

`Client` -> `Campaign` -> `CampaignProfile` (immutable versions of the ICP rules). Use the service
functions in `apps/campaigns/services.py`, never raw writes:

- `create_campaign(client, name, data, user=None)` creates a draft campaign and its version 1.
- `create_profile_version(campaign, data, user=None)` is the **only** way to change rules. `data`
  is any subset of the rule fields plus `change_note`; the rest carries over from the current
  version. It locks the campaign row, numbers the version, inserts it and moves
  `campaign.current_profile` in one transaction, leaves old versions untouched, and refuses an
  archived campaign (`ValidationError`) or identical rules (`ProfileUnchangedError`).
- `Campaign.current_profile` is NOT NULL with a deferred FK (campaign and v1 are inserted
  together). On PostgreSQL a composite FK also guarantees the profile belongs to the campaign.
- Lists (`countries` as ISO alpha-2 upper case, `outreach_languages` lower case, titles in
  preference order) are trimmed and de-duplicated by the service; `business_model` is
  `b2b` / `b2c` / `both`.
- The admin shows profile versions read-only, has no delete buttons (archive actions instead) and
  campaigns are created through the service, not the admin add form.

### Client API (`/api/v1/clients/`, issue #47)

`apps/campaigns/api/clients.py` (`ClientViewSet`, a `ClientScopedViewSet`): list, create, retrieve,
PUT/PATCH (name, notes), `POST {id}/archive/` and `POST {id}/restore/`. No DELETE. `status` is
read-only (changed only by archive/restore). List query params: `status` (`active`|`archived`),
`archived` (`false` default, `true`, `all`), `search` (name), `ordering` (`name`, `status`,
`created_at`, `updated_at`, prefix `-`), plus `page`/`page_size`. Names are unique ignoring case
among non-archived clients: a duplicate gives 400 `validation_error` with `details.name`. Writes
go through `create_client_with_admin` (creator becomes admin, audited), `update_client`,
`archive_client`, `restore_client` in `services.py`, so every change writes an `AuditLog` row with
the actor and diff. Archiving a client with queued or running jobs gives 409
`client_has_active_jobs`; an archived client is read-only (400). Per-action levels: [docs/permissions.md](../docs/permissions.md#client-endpoints-apiv1clients-issue-47).

Tests: `tests/factories.py` has `ClientFactory`, `CampaignFactory` (goes through
`create_campaign`; override rules with `profile__offer="..."`), `make_client()` and
`make_campaign()`. The concurrency test (`tests/test_profile_concurrency.py`) only runs on
PostgreSQL.

### Companies, research history and data sources (`apps/companies`)

`Campaign` -> `Company` -> `CompanyResearch` (append-only snapshots), each pointing at a
`DataSource`. Use `apps/companies/services.py`:

- `normalize_domain(website)` (`apps/companies/domain.py`) lowercases the host and drops scheme,
  credentials, port, path, a leading `www.` and trailing dots; IDN becomes punycode; IPs,
  single labels and junk give `None`. `Company.save()` always derives `domain` from `website`.
- `create_company(campaign, name, website="", *, profile_url, country, input_source, user,
  created_by_job_id)` returns a `CompanyResult`. Same normalized domain already in the campaign:
  nothing is inserted, `duplicate=True`, and an archived one is restored (`restored=True`). No
  domain: created, with `similar` / `warnings` listing active same-name companies (never
  auto-merged). Unique `(campaign, domain)` where domain is not null is also a database
  constraint (archived rows count).
- `add_research_snapshot(company, data_source, *, researched_at=None, **facts)` appends a
  snapshot (facts: `RESEARCH_FIELDS`; omitted means null = not found). The source must belong to
  the company's client. `researched_at` defaults to the source's `retrieved_at`.
- "Current" research has no `is_current` column: `CompanyResearch.objects.latest_for(company)`
  (or `company.latest_research`) picks the latest `researched_at` (ties: `created_at`, `id`),
  and `CompanyResearch.objects.current()` returns each company's latest row for lists.
- **`DataSource`** (`apps.companies.models.DataSource`, used by #41 for signals and contacts):
  append-only, client-scoped; fields `client`, `type` (`provider`, `website`, `news`,
  `manual`; `DataSourceType`), `name`, `url` (required unless manual or provider),
  `provider_reference`, `retrieved_at`, `evidence_date` (nullable, the date the source states),
  `created_by`, `created_at`. Create with `create_data_source(client, type, name, ...)`. Rows
  that reference a source must have the same `client_id` (see `CompanyResearch.sync_client`).
- Tests: `CompanyFactory`, `CompanyResearchFactory`, `DataSourceFactory`, `make_company()`,
  `make_research()`, `make_data_source()` in `tests/factories.py`.

### Assessments and decisions (`apps/research`)

`ICPAssessment` (fit only), `AIRecommendation` and `HumanDecision` are append-only, carry
`client`, and have no score. AI and human answers are different tables. Use
`apps/research/services.py`:

- `record_icp_assessment(company, fit, reasons, concerns=(), *, model_name, prompt_version,
  schema_version, raw_output=None, campaign_profile=None, company_research=None)`: `fit` is
  `strong` / `medium` / `weak`. The profile defaults to the campaign's current version and the
  research to the company's latest snapshot (none: `ValidationError`). Both must belong to the
  company, else `TenantMismatchError`. No signal input: a strong fit with no signals is valid.
- `record_ai_recommendation(icp_assessment, status, explanation, *, model_name, prompt_version,
  schema_version, raw_output=None)`: `status` is `add` / `hold` / `skip`.
- `record_human_decision(company, decision, user, *, ai_recommendation=None, note="",
  decided_at=None)`: never touches the recommendation. `ai_recommendation` is nullable (a decision
  with no AI answer is valid and is excluded from agreement).
- Current row: `Model.objects.latest_for(company)` and `Model.objects.current()` (latest per
  company; `decided_at` for decisions, `created_at` otherwise, ties by id).
- Agreement (Brief section 11): `HumanDecision.objects.agreement()` returns an `AgreementSummary`
  (`compared`, `agreed`, `disagreed`, `without_ai`, `rate`, `pairs`). Chain `.current()` for only
  each company's latest decision, or `.for_client(client)`. Also `.overrides()`, `.agreeing()`,
  `.with_ai_status()`.
- Factories: `make_icp_assessment()`, `make_ai_recommendation()`, `make_human_decision()`.

### Signals (`apps/research`) and contacts (`apps/companies`)

Placement: `Signal` lives in `apps/research` (timing evidence, used by M4/M5); `Contact` lives in
`apps/companies` (a person at the company). `DataSource` is not redefined: import it from
`apps.companies.models`.

- **Signal** (append-only, tenant): `company`, `data_source` (NOT NULL), `type` (the 13
  `SignalType` values of Brief section 7), `evidence`, `event_date` (NOT NULL), `detected_at`,
  `expires_at` (null = no rule yet, counts as fresh until M5), `supersedes` (one-to-one,
  reverse name `superseded_by`), optional AI provenance fields. No `active` flag, no stored
  Yes/No. An AI inference is never a source (there is no AI source type).
- `Signal.objects.current()` (not superseded), `.fresh(as_of=None)` (current and
  `expires_at` null or `> as_of`; stale at the exact expiry instant), `.for_company(c)`,
  `.latest_first()`.
- `services.create_signal(company, data_source, type, evidence, event_date, *, detected_at,
  expires_at, supersedes, user, ...)`, `supersede_signal(old, data_source, evidence,
  event_date, type=None)`, `retire_signal(old, data_source, reason)` (an `other` row that
  expires at once) and `company_trigger_state(company, as_of=None)` returning a `TriggerState`
  (`.triggered`, `.label` "Yes"/"No", `.signals`). Zero signals is a normal "No".
- **Contact** (mutable, archivable, tenant): `company`, `data_source`, `name`, `title`,
  `profile_url`, `email`, `email_status` (`unknown`/`not_found`/`unverified`/`verified`/
  `invalid`), `relevance_reason`, `rank`, `role` (`primary`/`secondary`/`none`), `erased_at`.
  Database: at most one non-archived primary and one secondary per company, unique profile URL
  among non-archived. Changes are to be logged as Activity later (#43).
- `apps.companies.services`: `create_contact`, `update_contact`, `set_contact_role` (taking a
  taken slot demotes the old holder to none), `restore_contact`; `Contact.objects.primary_for(
  company)`, `.for_company()`, `.ranked()`, `.active()`; `contact.erase_personal_data()` blanks
  personal fields, archives and keeps the id.
- Factories: `SignalFactory`, `ContactFactory`, `make_signal()`, `make_contact()`.

## Authentication

Decision and rationale: [ADR 0006](../docs/adr/0006-django-jwt-authentication.md). We use Django +
`djangorestframework-simplejwt` rather than Supabase Auth, to keep one stack and one user store.
There is no self-signup: admins create users (Django admin, `createsuperuser`, or
`User.objects.create_user(email=..., password=...)`).

**User model** (`apps/accounts/models.py`, `AUTH_USER_MODEL = "accounts.User"`): UUID primary key,
`email` (the login; stored lower-case, unique, enforced by a DB check constraint), `name`,
`is_active`, `is_staff`, `is_superuser`, `created_at`, `updated_at`. Because the project swaps the
user model, `accounts` migration `0001` must exist before anything that references users (the
token blacklist, admin, future apps); never change `AUTH_USER_MODEL` on a database that has
migrated. Roles and per-client access arrive with the tenancy model (issue #46).

**Endpoints** (all under `/api/v1/auth/`, JSON):

| Endpoint | Body | Success | Notes |
| --- | --- | --- | --- |
| `POST login/` | `{"email", "password"}` | 200 `{"access", "refresh"}` | generic 401 for any failure; throttled |
| `POST refresh/` | `{"refresh"}` | 200 `{"access", "refresh"}` (new pair) | rotates: the old refresh token is blacklisted |
| `POST logout/` | `{"refresh"}` | 204 | blacklists the refresh token; no access token needed |
| `GET me/` | none | 200 `{"id", "email", "name", "is_staff", "last_login", "created_at"}` | needs `Authorization: Bearer <access>` |

```bash
curl -X POST localhost:8000/api/v1/auth/login/ -H 'Content-Type: application/json' \
  -d '{"email": "YOUR_EMAIL", "password": "YOUR_PASSWORD"}'
curl localhost:8000/api/v1/auth/me/ -H 'Authorization: Bearer YOUR_ACCESS_TOKEN'
```

Every other endpoint requires the bearer token. A missing token is `401 not_authenticated`; a
malformed, expired, wrong-type or blacklisted one is `401 authentication_failed`. Session
authentication is not used by the API (the Django admin keeps its own session login).

**Token lifetimes and settings** (`SIMPLE_JWT` in `config/settings/base.py`): access token 15
minutes (`JWT_ACCESS_LIFETIME_MINUTES`), refresh token 7 days (`JWT_REFRESH_LIFETIME_DAYS`),
`ROTATE_REFRESH_TOKENS` and `BLACKLIST_AFTER_ROTATION` on, `UPDATE_LAST_LOGIN` on, HS256 signed with
`SECRET_KEY` (rotating it logs everyone out). Rotation means a refresh token works once: replaying a
used or logged-out token is a 401. Note that replay does not revoke the rest of the token family.
Deactivating a user (`is_active=False`) takes effect at once: login, refresh and already issued
access tokens are all rejected, because the user row is checked on every request. The blacklist tables grow; prune expired rows periodically with
`python manage.py flushexpiredtokens` (schedule it with Django-Q2 or cron).

**Login throttling.** Two limits apply to `POST login/`, both counted per request (successful or
not): per client IP (`API_THROTTLE_LOGIN`, default `20/min`) and per submitted email, case-insensitive
(`API_THROTTLE_LOGIN_EMAIL`, default `5/min`). Over the limit: `429 throttled` with `retry_after`.
Counters use Django's cache, which is per process by default (`LocMemCache`); with several gunicorn
workers or containers configure a shared cache backend so limits are enforced globally, and set
DRF's `NUM_PROXIES` when behind a reverse proxy so the real client IP is used.

**Password validators** (`AUTH_PASSWORD_VALIDATORS`): similarity to user attributes, minimum length
12, not a common password, not purely numeric. They run in the admin and in any future
password-setting endpoint; `create_user` itself does not validate.

**Refresh token as an httpOnly cookie (for the Next.js BFF).** Off by default: API clients get the
refresh token in the JSON body and send it back in the body. Set `AUTH_REFRESH_COOKIE_ENABLED=true`
and the refresh token is instead delivered only as a cookie (`login` and `refresh` responses
contain just `access`); `refresh` and `logout` read it from the cookie when the body has no
`refresh` (a body value always wins, so API clients keep working), and `logout` clears the cookie.
A rejected refresh cookie is cleared too.

| Setting / env var | Default | Meaning |
| --- | --- | --- |
| `AUTH_REFRESH_COOKIE_ENABLED` | `false` | turn cookie delivery on |
| `AUTH_REFRESH_COOKIE_NAME` | `abm_refresh` | cookie name |
| `AUTH_REFRESH_COOKIE_SECURE` | `true` | `Secure` flag; set `false` only for plain-HTTP local dev |
| `AUTH_REFRESH_COOKIE_SAMESITE` | `Lax` | `Strict`, `Lax` or `None` (`None` requires Secure) |
| (fixed) `AUTH_REFRESH_COOKIE_PATH` | `/api/v1/auth/` | the cookie is only sent to the auth endpoints |

The cookie is always `HttpOnly`. CSRF: the auth endpoints accept JSON only (not form posts) and the
cookie is `SameSite=Lax` or stricter, so a cross-site page cannot make the browser send it with a
state-changing request; keep the BFF and API on the same site, and use `Strict` if the BFF
can tolerate it. The BFF should keep the access token in memory and call `refresh/` on page load.

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

## OpenAPI schema and API client

The API is described by an OpenAPI 3.0 schema generated with
[drf-spectacular](https://drf-spectacular.readthedocs.io/). The generated file is **committed** at
[`docs/api/openapi.yaml`](../docs/api/openapi.yaml) and is the contract the frontend client
(`frontend/src/lib/api/`) is generated from, so backend and frontend cannot drift silently.

| URL | What |
| --- | --- |
| `GET /api/v1/schema/` | the schema (YAML; `?format=json` for JSON) |
| `GET /api/v1/docs/` | Swagger UI (use "Authorize" with an access token to call endpoints) |
| `GET /api/v1/redoc/` | ReDoc |

**Are the docs enabled in production? No, by default.** All three URLs answer only while the
`API_DOCS_ENABLED` setting is true (env var, see `docs/environment.md`): `true` in
`config.settings.dev`, `false` in base, prod and test. Otherwise they return the normal `404
not_found` envelope. The committed `docs/api/openapi.yaml` is the production-safe copy of the
contract. Turn the flag on for a staging/preview environment if you want live docs there. The UIs load their
JavaScript from a CDN, which is another reason to keep them out of production.

What the schema documents:

- **Security**: a `jwtAuth` HTTP bearer scheme (`Authorization: Bearer <access>`). Endpoints that
  need no token (login, refresh, logout, health, API root) are marked `security: [{}]`.
- **Errors**: the standard envelope (see "Logging, request IDs and API errors") is the
  `ErrorEnvelope` component (`{"error": ErrorBody}`, `ErrorBody` = `code`, `message`, `details`,
  `request_id`). Every operation lists it for its error statuses (400/401/429/500 as applicable;
  `/readyz` 503 returns the readiness body instead).
- **Operations**: each has a stable `operationId` and a tag: `auth_login`, `auth_refresh`,
  `auth_logout`, `auth_me` (tag `auth`); `health_live` (`/healthz`), `health_ready` (`/readyz`)
  (tag `health`); `api_root` (tag `meta`). Paths are the real URLs (`/api/v1/auth/login/`,
  `/healthz`). Operation ids become the names clients use, so do not rename them casually.

### Adding or changing an endpoint

1. Decorate the view method with `@extend_schema(tags=[...], operation_id="<tag>_<action>",
   responses={200: MySerializer, **error_responses(400, 401, 500)})`
   (`error_responses` is in `apps/core/schema.py` and produces the envelope responses).
2. Regenerate and commit the schema: `make api-schema` (from the repo root), which runs
   `DJANGO_SETTINGS_MODULE=config.settings.test uv run python manage.py spectacular --validate
   --fail-on-warn --file ../docs/api/openapi.yaml`. It needs no database or `.env`. Then
   `make api-client` also regenerates the frontend types.
3. Fix any warning spectacular prints (unannotated `APIView`, unresolvable serializer, duplicate
   component name). The generation is **warning-free by contract**: `--fail-on-warn` is used both
   by the make target and by the test.

### CI check: a stale schema fails

`tests/test_openapi.py` regenerates the schema in memory with `--validate --fail-on-warn` and
fails when it produces any warning, when it does not equal `docs/api/openapi.yaml`, or when the
operation ids, tags, security scheme or error envelope drift. It runs with the normal backend
test suite, so changing a serializer or view without running `make api-schema` fails CI. The frontend
side (generated types out of date) is checked by `make api-check`, see `frontend/README.md`.

## Tests

```bash
uv run pytest                      # tests + coverage (terminal report, coverage.xml)
uv run pytest tests/examples -k view   # subset
uv run pytest --no-cov             # skip coverage for a quick loop
```

Coverage is configured in `pyproject.toml` (branch coverage over `apps/` and `config/`,
fails under 90%; `coverage.xml` is written for CI to pick up).

Harness (`tests/`): `conftest.py` provides `api_client`, `user` and `auth_client` fixtures
(`auth_client` sends a real JWT access token); `factories.py` holds factory_boy
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
| `not_authenticated` / `authentication_failed` | 401 (with `WWW-Authenticate: Bearer`) | null |
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
(`page_size` query param, max 100), JWT bearer authentication (no session auth), anon/user throttling enabled with configurable rates
(disabled in test), `IsAuthenticated` as the default permission (the API root is public).
