# Infra

Home for Docker, environment and deployment files.

## Stack

- Docker Compose for local development (`docker-compose.yml` at the repo root).
- PostgreSQL for data and for the Django-Q2 job queue. There is no Redis and no Celery.
- Services: `db`, `api`, `web`, and `worker` (placeholder until issue #25).

## Local development

```bash
cp .env.example .env    # replace the YOUR_* placeholders
docker compose up --build
```

| Service | Image / build | Host port (variable) | Health |
| --- | --- | --- | --- |
| `db` | `postgres:16-alpine`, volume `pgdata` | 5432 (`DB_PORT`) | `pg_isready` |
| `api` | `backend/Dockerfile`, dev command | 8000 (`API_PORT`) | GET `/api/v1/` |
| `web` | `frontend/Dockerfile` `deps` stage, `npm run dev` | 3000 (`WEB_PORT`) | GET `/` |

- `api` waits for `db` to be healthy, applies migrations, then runs `runserver`.
- `backend/` and `frontend/` are bind-mounted for hot reload. The Python virtualenv and `node_modules` live in named volumes so the mounts do not hide them. After changing `uv.lock` or `package.json`, run `docker compose down -v` (this also wipes the database) and rebuild with `docker compose up --build`, or remove just the `abm-engine_api_venv` / `abm-engine_web_node_modules` volumes with `docker volume rm`.
- The Dockerfiles in `backend/` and `frontend/` stay production-style (gunicorn, Next.js standalone build). Compose overrides the command for development.
- `NEXT_PUBLIC_API_BASE_URL` is read by the browser, so it must point at the host port of the API.
- `docker-compose.override.yml` is git-ignored for personal tweaks.

### Worker (issue #25)

When Django-Q2 lands, uncomment the `worker` block in `docker-compose.yml`. It runs `python manage.py qcluster` from the same image and uses Postgres as its queue.

### Make targets

`make up`, `make down`, `make logs`, `make migrate`, `make test`, `make shell`.

## Rules

- Configuration comes from environment variables. Document every variable in `.env.example` with placeholders such as `YOUR_API_KEY`.
- Never commit real credentials. `.env` files are git-ignored.
- Production deployment and hardening are a later milestone.
