# Smoke-test checklist

A quick manual check that a fresh setup works. Run it after following
[onboarding.md](onboarding.md), and again whenever you change Compose, the
Dockerfiles or settings. It should take about five minutes once images are
built.

Each step says what you should see. Some steps cannot pass yet because the
feature belongs to a later issue; those are marked **Pending** and are not
failures.

Start from a clean state if you can:

```bash
docker compose down -v      # deletes local data
docker compose up --build -d
```

## 1. Stack is up

- [ ] `docker compose ps` lists `db`, `api` and `web`, all `running` and
      `healthy` (give `web` up to a minute).
- [ ] `docker compose logs api` shows migrations applying (`Applying ...`, or
      `No migrations to apply` on a later run) and then the Django dev server
      starting on `0.0.0.0:8000`, with no tracebacks.
- [ ] **Pending (#25):** a `worker` service. It is commented out in
      `docker-compose.yml` until Django-Q2 is added, so only three services are
      expected today.

## 2. API health

There is no dedicated `/health` endpoint yet. Until one exists, the API root is
what the Compose healthcheck uses, and it is the health signal for now.

- [ ] `curl -i http://localhost:8000/api/v1/` returns `200 OK` with JSON like:

  ```json
  {"name": "ABM Engine API", "version": "v1", "links": {"self": "http://localhost:8000/api/v1/"}}
  ```

  (the exact `version` value depends on the DRF versioning settings).
- [ ] The database is reachable. This is implied by step 1, because `api` only
      starts after `db` is healthy and migrations succeed. To check it
      directly:
      `docker compose exec db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`
      prints `accepting connections`.
- [ ] **Pending (#28):** a health endpoint that reports database and queue
      status. When it lands, replace this section with a check of that endpoint.

## 3. Worker runs a task

- [ ] **Pending (#25):** this whole step. There is no Django-Q2 and no worker yet,
      so no task can run end to end. When it lands, the expected check is: start
      the `worker` service, enqueue the smoke-test task, and see it complete in
      the worker log and in the job status. Fill in the exact commands then.
- [ ] Today you can at least confirm the queue backend is Postgres and nothing
      else is needed: no Redis or Celery container appears in `docker compose ps`.

## 4. Frontend loads

- [ ] Open http://localhost:3000. It redirects to `/dashboard` and shows the app
      shell (sidebar, header, placeholder content). No error page.
- [ ] `/campaigns` and `/companies` also load their placeholder pages.
- [ ] A made-up path such as `/nope` shows the not-found page.
- [ ] The browser console shows no red errors on load.
- [ ] **Pending:** pages that show real API data. The placeholder pages do not
      call the API yet, so this check cannot confirm the frontend-to-API link.
      Until then, confirm `NEXT_PUBLIC_API_BASE_URL` in `.env` is
      `http://localhost:8000/api` (the browser must be able to reach it).

## 5. Tests and checks

- [ ] `make test` (or `cd backend && uv sync && uv run pytest`) passes.
- [ ] `cd frontend && npm install && npm run typecheck && npm run build` passes.
- [ ] **Pending:** frontend lint and tests (#31) and CI. They do not exist on
      `main` yet.

## 6. Optional: admin and seed data

- [ ] Run the seed command from [onboarding.md](onboarding.md#4-optional-create-a-dev-superuser),
      then log in at http://localhost:8000/admin/.

## 7. Clean shutdown

- [ ] `docker compose down` stops everything. Running `docker compose up -d`
      again brings it back with your data intact (the `pgdata` volume).

## Recording a run

When a teammate runs this from scratch (issue #36 asks for that), note the date,
OS, what they hit and what was fixed in the docs. If a step was unclear or
failed, fix [onboarding.md](onboarding.md) in the same PR.

| Date | Person | OS | Result | Notes |
| --- | --- | --- | --- | --- |
| | | | | |
