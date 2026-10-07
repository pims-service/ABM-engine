# Continuous integration

The pipeline is `.github/workflows/ci.yml` (issue #32). It runs on every pull
request and on every push to `main`. A newer push to the same PR cancels the
run it replaces. The workflow has read-only `contents` permission and uses no
repository secrets: the database password and Django secret key in it are
throwaway values that live only on the runner.

## Jobs and required checks

These job names are the status checks to mark as **required** in the branch
protection rule for `main` (Settings > Branches; needs a repository admin, it
is not configured by the workflow):

| Check name | What it does | Run it locally |
| --- | --- | --- |
| `Backend (lint, types, tests)` | `uv sync --frozen`, ruff check, ruff format --check, mypy, `makemigrations --check`, pytest against a Postgres 16 service (so the Postgres-only tests run). Uploads `coverage.xml` as the `backend-coverage` artifact. | see below |
| `Frontend (lint, types, tests, build)` | `npm ci`, lint, format:check, typecheck, vitest, `next build` with a placeholder `NEXT_PUBLIC_API_BASE_URL`. | see below |
| `Frontend (Playwright e2e)` | Installs Chromium with system dependencies and runs `npm run test:e2e`. The HTML report is uploaded as `playwright-report` when it fails. | see below |
| `Pre-commit hooks` | `pre-commit run --all-files` (whitespace and EOL hygiene, YAML/JSON checks, detect-secrets, ruff, prettier and eslint wrappers). | `pre-commit run --all-files` |
| `Docker images build` | Builds the backend and frontend images. Nothing is pushed. | see below |
| `Secret scan (gitleaks)` | gitleaks over the full git history. | see below |

### Backend steps worth knowing by name (issue #53)

Inside the `Backend (lint, types, tests)` job (the job name is unchanged, it is the required
check):

| Step | What it does | Run it locally |
| --- | --- | --- |
| `Invariant tests (history, ICP vs trigger, tenant isolation)` | `pytest -m invariants`: the brief's hard rules (append-only history, ICP fit vs trigger, AI vs human, no outreach without an `add` decision, tenant isolation on every model and endpoint). Runs before the main suite so a regression is visible by name. | `uv run pytest -m invariants --no-cov` |
| `Pytest (with coverage)` | Everything else (`-m "not invariants" --cov-append`), applying the 90% gate to both runs together. | `uv run pytest` |
| `Coverage of models and permissions (85% each)` | `scripts/check_module_coverage.py`: every `apps/*/models.py` and `core/permissions.py`, `tenancy.py`, `roles.py` must reach 85% by itself. | after `uv run pytest`: `uv run python scripts/check_module_coverage.py` |

How to extend the group (new model, new endpoint): [backend/README.md](../backend/README.md#invariant-tests-testsinvariants-issue-53).
The job timeout is 25 minutes (the invariant group adds about 4 minutes on PostgreSQL).

Dependencies are cached (uv cache keyed on `backend/uv.lock`, npm cache keyed
on `frontend/package-lock.json`, Playwright browsers keyed on the Playwright
version, pre-commit environments, Docker layers), so a typical run should stay
well under the 8 minute target.

The OpenAPI freshness check from the issue is not part of the pipeline yet:
the backend does not generate an OpenAPI schema. Add the check when a schema
generator lands.

## Run each job locally

Backend (needs a reachable Postgres; see
[backend/README.md](../backend/README.md) for starting one):

```bash
cd backend
export DATABASE_URL=postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@localhost:5432/YOUR_DB_NAME
export SECRET_KEY=YOUR_SECRET_KEY
uv sync --frozen
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

Frontend:

```bash
cd frontend
npm ci
npm run lint && npm run format:check && npm run typecheck
npm test
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api npm run build
npx playwright install --with-deps chromium   # once
npm run test:e2e
```

Docker images and secret scan (from the repository root):

```bash
docker build ./backend
docker build ./frontend
docker run --rm -v "$PWD:/repo" -w /repo zricethezav/gitleaks:v8.21.2 detect --source . --redact
```

## Notes

- A failing hook that edits files (for example trailing whitespace) fails the
  `Pre-commit hooks` job; run `pre-commit run --all-files` locally and commit
  the result.
- Action versions are pinned by major version. Update them deliberately, in a
  PR of their own.
- A false positive from gitleaks or detect-secrets is handled with an inline
  `# pragma: allowlist secret` (detect-secrets) or a `.gitleaks.toml`
  allowlist entry; never with real values. See
  [environment.md](environment.md#secrets-handling).
