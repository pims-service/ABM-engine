# ABM Engine backend

Django 5.2 + Django REST Framework API. PostgreSQL for data. Background jobs will use
Django-Q2 on a Postgres-backed queue (no Redis, no Celery); that wiring is a separate issue.
JWT auth (SimpleJWT) is also a separate issue.

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
config/urls.py                            /admin/ and /api/v1/
apps/{accounts,campaigns,companies,research,integrations,ai,core}   empty apps (AppConfigs registered)
tests/                                    pytest smoke tests
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

## Tests

```bash
uv run pytest
```

Tests use `config.settings.test`, which needs no `.env` or database server: it supplies throwaway
values and uses in-memory SQLite. SQLite is a fallback for the test settings only; dev and prod
require a Postgres `DATABASE_URL`.

## Docker

```bash
docker build -t abm-backend backend
docker run --rm -p 8000:8000 \
  -e SECRET_KEY=YOUR_SECRET_KEY -e DATABASE_URL=postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@HOST:5432/YOUR_DB_NAME \
  -e ALLOWED_HOSTS=localhost -e SECURE_SSL_REDIRECT=False abm-backend
```

The image runs gunicorn with `config.settings.prod`. Compose wiring is handled in another issue.

## Environment variables

See `.env.example`. Values are read via django-environ; a local `.env` is loaded if present and
real environment variables take precedence.

| Variable | Required | Notes |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | no | `manage.py` defaults to `config.settings.dev`; wsgi/asgi default to `config.settings.prod` |
| `SECRET_KEY` | yes (dev/prod) | no default, startup fails if missing |
| `DATABASE_URL` | yes (dev/prod) | Postgres URL |
| `DEBUG` | no | dev defaults to true, prod forced false |
| `ALLOWED_HOSTS` | prod: yes | comma-separated |
| `API_PAGE_SIZE` | no | default 25 |
| `API_THROTTLE_ANON`, `API_THROTTLE_USER` | no | DRF rates, default `100/hour`, `1000/hour` |
| `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `CSRF_TRUSTED_ORIGINS` | no | prod hardening |

## DRF defaults

JSON renderer only (browsable API added in dev), JSON parser, page-number pagination
(`page_size` query param, max 100), anon/user throttling enabled with configurable rates
(disabled in test), `IsAuthenticated` as the default permission (the API root is public).
