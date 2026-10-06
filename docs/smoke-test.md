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
- [ ] The `worker` service is listed too and becomes `healthy` (its healthcheck
      is `python manage.py worker_healthcheck`, which needs a fresh Django-Q2
      heartbeat).

## 2. API health

Two unauthenticated probe endpoints live at the root of the API (not under
`/api/v1/`). The `api` Compose healthcheck uses `/healthz`.

- [ ] `curl -i http://localhost:8000/healthz` returns `200 OK` with
      `{"status": "ok"}`. It touches no dependency, so it answers even when
      the database is down.
- [ ] `curl -i http://localhost:8000/readyz` returns `200 OK` with a per-check
      breakdown:

  ```json
  {"status": "ok", "checks": {"database": {"status": "ok"}, "migrations": {"status": "ok"},
   "worker": {"status": "ok", "clusters": 1, "heartbeat_age_seconds": 0.5}}}
  ```

- [ ] Stop the worker (`docker compose stop worker`), wait about 30 seconds and
      repeat the `/readyz` call: it returns `503` with `"worker": {"status": "fail",
      "detail": "no recent worker heartbeat"}` while `/healthz` stays `200`.
      `docker compose start worker` brings `/readyz` back to `200`.
- [ ] Stop the database (`docker compose stop db`): `/readyz` returns `503`
      with `"database": {"status": "fail", ...}` (the other checks show
      `skipped`), `/healthz` stays `200`. `docker compose start db` recovers it.
- [ ] `curl -i http://localhost:8000/api/v1/` still returns `200 OK` with the API
      name, version and a self link.
- [ ] The database is reachable directly:
      `docker compose exec db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`
      prints `accepting connections`.

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
