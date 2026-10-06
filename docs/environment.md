# Environment variables and secrets

This page is the single source of truth for every environment variable the project reads.
The `.env.example` files and `docker-compose.yml` must agree with it; a backend test
(`backend/tests/test_env_docs.py`) fails when they drift. Add a variable here first.

All examples are placeholders. Never put a real value in this file, in an `.env.example`,
in code, in tests or in a commit message.

## Where configuration lives

| File | Used by | Copy to |
| --- | --- | --- |
| `.env.example` (root) | `docker-compose.yml` (db, api, worker, web) | `.env` |
| `backend/.env.example` | Django run directly on the host (`uv run ...`) | `backend/.env` |
| `frontend/.env.example` | Next.js run directly on the host | `frontend/.env.local` |

Real environment variables always win over a `.env` file. All copies are git-ignored.

## Variable reference

- **Service**: which part reads it (`db`, `api`, `worker`, `web`, or `compose` for Docker Compose itself).
- **Required**: `yes` means startup fails (or compose refuses to start) without it. The backend
  checks this in `backend/config/env_validation.py` and lists every missing or invalid variable in
  one error that never contains values.
- **Example files**: which `.env.example` files list the variable (`-` means none).

| Variable | Service | Required | Default | Example placeholder | Example files | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `POSTGRES_USER` | db, compose | yes (compose) | none | `YOUR_DB_USER` | root | Database user. Only read when the data volume is first created. |
| `POSTGRES_PASSWORD` | db, compose | yes (compose) | none | `YOUR_DB_PASSWORD` | root | Secret. Only read when the data volume is first created. |
| `POSTGRES_DB` | db, compose | yes (compose) | none | `YOUR_DB_NAME` | root | Database name. |
| `DATABASE_URL` | api, worker | yes | none (compose builds it) | `postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@localhost:5432/YOUR_DB_NAME` | backend | Secret (contains the password). Must be a `postgres://` URL. |
| `DJANGO_SETTINGS_MODULE` | api, worker | no | `config.settings.dev` for `manage.py`; `config.settings.prod` for wsgi/asgi | `config.settings.dev` | backend | `config.settings.test` skips the startup check and is for tests only. |
| `SECRET_KEY` | api, worker | yes | none | `YOUR_SECRET_KEY` | root, backend | Secret. Rejected while it is still a `YOUR_*` placeholder; at least 32 characters in production. |
| `DEBUG` | api, worker | no | `True` in dev, forced `False` in prod | `True` | root, backend | `true`/`false`. |
| `ALLOWED_HOSTS` | api | prod only | `localhost,127.0.0.1,[::1]` in dev | `localhost,127.0.0.1` | root, backend | Comma-separated host names. |
| `API_DOCS_ENABLED` | api | no | `true` in dev, `false` everywhere else | `false` | backend | `true`/`false`. Serves the OpenAPI schema and Swagger UI/ReDoc at `/api/v1/schema/`, `/docs/`, `/redoc/` (404 when off). Keep off in production. |
| `CSRF_TRUSTED_ORIGINS` | api | no | empty | `https://YOUR_DOMAIN` | backend | Production only, comma-separated origins. |
| `SECURE_SSL_REDIRECT` | api | no | `True` in prod | `True` | backend | Production only. |
| `SECURE_HSTS_SECONDS` | api | no | `3600` | `3600` | backend | Production only. Integer. |
| `API_PAGE_SIZE` | api | no | `25` | `25` | backend | DRF default page size. Integer. |
| `API_THROTTLE_ANON` | api | no | `100/hour` | `100/hour` | backend | DRF rate for anonymous users. |
| `API_THROTTLE_USER` | api | no | `1000/hour` | `1000/hour` | backend | DRF rate for authenticated users. |
| `API_THROTTLE_LOGIN` | api | no | `20/min` | `20/min` | backend | Login attempts per client IP (DRF rate). |
| `API_THROTTLE_LOGIN_EMAIL` | api | no | `5/min` | `5/min` | backend | Login attempts per submitted email (DRF rate). |
| `JWT_ACCESS_LIFETIME_MINUTES` | api | no | `15` | `15` | backend | Access token lifetime in minutes. Integer. |
| `JWT_REFRESH_LIFETIME_DAYS` | api | no | `7` | `7` | backend | Refresh token lifetime in days. Integer. |
| `AUTH_REFRESH_COOKIE_ENABLED` | api | no | `false` | `false` | backend | `true`/`false`. Deliver the refresh token as an httpOnly cookie (BFF) instead of in the JSON body. |
| `AUTH_REFRESH_COOKIE_SECURE` | api | no | `true` | `true` | backend | `true`/`false`. `Secure` flag of the refresh cookie; `false` only for plain-HTTP local dev. |
| `AUTH_REFRESH_COOKIE_NAME` | api | no | `abm_refresh` | `abm_refresh` | backend | Name of the refresh cookie. |
| `AUTH_REFRESH_COOKIE_SAMESITE` | api | no | `Lax` | `Lax` | backend | `Lax`, `Strict` or `None` (`None` needs Secure). |
| `Q_WORKERS` | worker | no | `2` | `2` | root, backend | Django-Q2 worker processes. Integer. |
| `Q_TASK_TIMEOUT` | worker | no | `300` | `300` | root, backend | Hard per-task limit in seconds. Integer. |
| `Q_TASK_RETRY` | worker | no | `360` | `360` | root, backend | Redelivery delay in seconds; must be greater than `Q_TASK_TIMEOUT`. |
| `LOG_LEVEL` | api, worker | no | `INFO` | `INFO` | root, backend | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `DEV_SUPERUSER_EMAIL` | api | no | `admin@example.com` | `YOUR_EMAIL` | root, backend | Read by `seed_dev_data` only. |
| `DEV_SUPERUSER_PASSWORD` | api | no | none (no superuser is created) | `YOUR_DEV_ADMIN_PASSWORD` | root, backend | Secret. Read by `seed_dev_data` only; local development only. |
| `DEV_SEED_USER_PASSWORD` | api | no | none (sample users cannot log in) | `YOUR_DEV_SEED_PASSWORD` | root, backend | Secret. Read by `seed_dev_data` only; local development only. Password for the sample admin/manager/reviewer/viewer users. |
| `NEXT_PUBLIC_API_BASE_URL` | web | yes | `http://localhost:8000/api` in compose | `http://localhost:8000/api` | root, frontend | Public: shipped to browsers. Never put a secret in a `NEXT_PUBLIC_*` variable. |
| `NEXT_TELEMETRY_DISABLED` | web | no | `1` (set in compose) | `1` | - | Fixed in `docker-compose.yml`; not meant to be changed. |
| `WATCHPACK_POLLING` | web | no | `true` (set in compose) | `true` | - | Fixed in `docker-compose.yml` for bind mounts on Windows/macOS. |
| `DB_PORT` | compose | no | `5432` | `5432` | root | Host port of the db service. |
| `API_PORT` | compose | no | `8000` | `8000` | root | Host port of the api service. Keep `NEXT_PUBLIC_API_BASE_URL` in step. |
| `WEB_PORT` | compose | no | `3000` | `3000` | root | Host port of the web service. |
| `LLM_API_KEY` | api | reserved | none | `YOUR_LLM_API_KEY` | backend | Secret. Reserved name for a future LLM provider key; no code reads it yet. |
| `DATA_PROVIDER_API_KEY` | api | reserved | none | `YOUR_DATA_PROVIDER_API_KEY` | backend | Secret. Reserved for a future data provider; no code reads it yet. |
| `CRM_API_KEY` | api | reserved | none | `YOUR_CRM_API_KEY` | backend | Secret. Reserved for a future CRM integration; no code reads it yet. |

Variables marked `reserved` only fix the naming convention. Real provider names and per-tenant
credentials will be added with the integrations issues, and this table updated at the same time.

## Startup validation

When Django settings load (any module except `config.settings.test`), the backend checks the
variables above and refuses to start if any required one is missing or any provided one is
invalid. The error lists every problem at once, for example:

```
django.core.exceptions.ImproperlyConfigured: Invalid environment configuration:
  - SECRET_KEY: missing (required)
  - DATABASE_URL: missing (required)
  - Q_WORKERS: invalid, must be an integer
See docs/environment.md for every variable; copy .env.example to .env to start.
```

Messages name variables and rules only, never values, so a failed boot cannot leak a secret into
logs. Compose has its own check for the three `POSTGRES_*` variables and `SECRET_KEY`
(`set X in .env`). The frontend fails at startup if `NEXT_PUBLIC_API_BASE_URL` is missing.

## Secrets handling

**Rules**

1. Never commit a `.env` file or any real credential: not in code, tests, docs, examples, issues
   or commit messages. `.gitignore` excludes `.env`, `.env.*` (except `.env.example`), `*.pem`,
   `*.key` and `secrets/`.
2. Examples use obvious placeholders (`YOUR_API_KEY`, `YOUR_SECRET_KEY`). A test rejects anything
   in an `.env.example` that looks like a real key.
3. Secret values are read only from the environment. Do not hard-code them or add defaults for them.
4. Never log secrets. Console logging scrubs the values of fields named like `password`, `token`,
   `api_key`, `secret`, `authorization` and URL passwords (`backend/apps/core/logging.py`).
   Scrubbing is a safety net; do not log credentials in the first place.
5. Anything in a `NEXT_PUBLIC_*` variable is public. Keep secrets on the backend.
6. Local development secrets protect only your own machine; use throwaway values and never reuse a
   real password.

**Where real values live**

| Environment | Where | Who can read |
| --- | --- | --- |
| Local development | Your untracked `.env` / `backend/.env` / `frontend/.env.local` | You |
| CI | CI platform secrets, injected as environment variables for the jobs that need them. Test jobs need none: backend tests use throwaway values from `config.settings.test` | CI only |
| Staging / production | The deployment platform's secret store (or a secrets manager), injected as environment variables at runtime. Not in the image, not in the repo | Operators |

**Rotation**

- Rotate immediately if a value may have leaked, and tell the team. Deleting it in a later commit
  is not enough, because the value stays in git history.
- Rotate on a schedule for production keys (suggested: every 90 days) and whenever someone with
  access leaves.
- Rotating `SECRET_KEY` invalidates existing sessions. Rotating `POSTGRES_PASSWORD` also needs
  `ALTER USER` in the database, because the variable only applies when the volume is created.

**Provider credentials (later)**

LLM, data-provider and CRM credentials arrive with the integrations work. The rules above apply
now. When they are stored per tenant they must be encrypted at rest, decrypted only in memory at
the point of use, excluded from API responses, logs and error reports, and rotatable without a
deploy. That design is made in the integrations issues, not here.

**Scanning**

- Locally, the `detect-secrets` pre-commit hook blocks commits that contain credentials (see
  [conventions](conventions.md#secrets)). If it flags a false positive, add
  `# pragma: allowlist secret` on that line.
- In the test suite, `backend/tests/test_env_docs.py` checks that every `.env.example` contains
  placeholders only and matches this page.
- In CI, the `Pre-commit hooks` job runs detect-secrets and the `Secret scan (gitleaks)` job scans the full git history (see [ci.md](ci.md)). CI jobs need no repository secrets.
