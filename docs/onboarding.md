# Developer onboarding

This page gets you from a fresh machine to a running ABM Engine stack. Plan on
about 30 minutes, most of it Docker image downloads the first time.

When you are done, walk through the [smoke-test checklist](smoke-test.md) to
confirm everything works.

A note on honesty: the project is early (milestone M0). Some things you might
expect are not built yet, such as the background worker. Where that matters, this page says so and names the issue.

## 1. Prerequisites

| Tool | Why you need it | Check |
| --- | --- | --- |
| Git | Clone the repo | `git --version` |
| Docker with Compose v2 | Runs Postgres, the API and the web app | `docker compose version` |
| [uv](https://docs.astral.sh/uv/) | Python and dependency manager for the backend (Python 3.12 is pinned) | `uv --version` |
| Node 20 and npm, **or** Docker only | Frontend. Node 20 matches the Docker image. | `node --version` |

Notes:

- If you only plan to run everything through Docker Compose, you do not
  strictly need uv or Node on your machine. You do need them if you want to run
  tests, linters or the dev servers outside containers, or let your editor pick
  up the virtualenv and `node_modules`.
- `make` is optional. It is a thin wrapper over `docker compose`. On Windows it
  is easiest to use Git Bash or WSL, or just run the `docker compose` commands
  shown below.
- Optional but recommended: [pre-commit](https://pre-commit.com) for the commit
  checks described in [conventions.md](conventions.md).

## 2. Clone and configure

```bash
git clone https://github.com/pims-service/ABM-engine.git
cd ABM-engine
cp .env.example .env
```

Open `.env` and replace the placeholders:

| Variable | What to put |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Any local values you like. They only protect your own dev database. |
| `SECRET_KEY` | A random string. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(50))"` |

The other values in `.env.example` have working defaults. Leave them unless a
port clashes (see [Troubleshooting](#ports-already-in-use)).

`.env` is git-ignored. Never commit real credentials; see
[CONTRIBUTING.md](../CONTRIBUTING.md). Every variable, and the secrets policy, is in
[environment.md](environment.md). The API refuses to start while `SECRET_KEY` is still the
`YOUR_SECRET_KEY` placeholder, and its error names each missing variable.

## 3. Start the stack

```bash
docker compose up --build
```

(or `make up` to run it in the background). The first build takes a few
minutes. You should end up with three services:

| Service | Where | What it is |
| --- | --- | --- |
| `db` | `localhost:5432` | PostgreSQL 16 |
| `api` | http://localhost:8000/api/v1/ | Django dev server. Runs migrations when it starts. |
| `web` | http://localhost:3000 | Next.js dev server |

Check that they are healthy:

```bash
docker compose ps
```

All three should show `healthy` after a minute or so (`web` can take around
30 seconds on first start). Source code in `backend/` and `frontend/` is
bind-mounted, so edits reload automatically.

There is no `worker` service yet. The Django-Q2 worker arrives with issue #25;
until then the `worker` block in `docker-compose.yml` is commented out.

## 4. Optional: create a dev superuser

Migrations run automatically when `api` starts. To log in to the Django admin
at http://localhost:8000/admin/ you need a user. The seed command creates one
from environment variables and does nothing without a password:

```bash
docker compose exec -e DEV_SUPERUSER_EMAIL=YOUR_EMAIL \
  -e DEV_SUPERUSER_PASSWORD=YOUR_PASSWORD \
  api python manage.py seed_dev_data
```

It is safe to run twice. There is no sample company or campaign data yet; that
comes with the product models in M1.

## 5. Run the tests and checks

Backend tests need no database server and no `.env` (they use in-memory
SQLite):

```bash
cd backend
uv sync
uv run pytest
```

Or without installing anything locally: `make test`.

Backend lint and types, from `backend/`:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

Frontend, from `frontend/`:

```bash
cp .env.example .env.local
npm install
npm run dev          # only if you are not already running the web container
npm run typecheck
npm run build
```

ESLint, Prettier and frontend tests are not set up on `main` yet (issue #31),
and there is no CI yet either.

## 6. Install the commit hooks

```bash
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Commit messages must follow Conventional Commits. Details are in
[conventions.md](conventions.md).

## 7. Running without Docker for the app (optional)

If you want the Django or Next.js process on your host (for debugging, say),
keep Postgres in Docker and run the rest locally:

```bash
docker compose up -d db
cd backend
cp .env.example .env     # set DATABASE_URL to match the values in the root .env
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

The `DATABASE_URL` in `backend/.env` looks like
`postgres://YOUR_DB_USER:YOUR_DB_PASSWORD@localhost:5432/YOUR_DB_NAME`, using the
same user, password and database name you put in the root `.env`. Stop the
`api` container first if it is running, since both want port 8000.

More backend detail (migrations, resetting the database, backups) is in
[backend/README.md](../backend/README.md).

## 8. Where things live

- [docs/adr/](adr/README.md): architecture decision records. Read these to
  understand why the stack and the data model look the way they do.
- [docs/conventions.md](conventions.md): commit messages, branch names, PR
  rules and the pre-commit hooks.
- [CONTRIBUTING.md](../CONTRIBUTING.md): ground rules, review expectations.
- [infra/README.md](../infra/README.md): Compose services, ports, volumes.
- [backend/README.md](../backend/README.md) and
  [frontend/README.md](../frontend/README.md): per-app details.

## Windows notes

Most people on the team use Windows, so these are worth reading even if
nothing has gone wrong yet.

### Python crashes depending on the current folder

Some Windows machines have shown Python (and so `uv`) crashing or misbehaving
when the shell's current directory is inside certain folders. We have not
pinned down one root cause. What has worked:

- Keep the repo on a short, plain path such as `C:\dev\ABM-engine`, not under
  OneDrive, Desktop, Documents or a folder with spaces or unusual characters in
  its name.
- If Python dies with an odd error right after you `cd` somewhere, move to a
  different directory (for example the repo root) and run it again.
- Running the stack with Docker Compose avoids the problem, because Python
  runs inside the container.

If you find the exact trigger, please add it here.

### Corporate TLS interception

Some company networks intercept HTTPS and re-sign traffic with their own
certificate authority (CA). Tools that bring their own list of trusted CAs then
fail with certificate errors (`unable to get local issuer certificate`, `self
signed certificate in certificate chain` and similar).

For uv on the host, tell it to use the operating system's certificate store:

```bash
uv --system-certs sync
uv --system-certs run pytest
```

(The flag goes before the subcommand. It needs a recent uv; if your version
does not know it, upgrade uv and check `uv --help`.)

For Docker image builds, the build runs inside a container that does not know
your company CA, so `uv sync` (backend), `npm ci` (frontend) and package
installs fail the same way. The Dockerfiles in this repo do **not** have a
built-in switch for this today. The approach that works is:

1. Export your company root CA as a PEM file (ask IT, or export it from the
   Windows certificate manager as Base-64 `.cer`).
2. Make the build trust it. For a local, uncommitted experiment, copy the PEM
   into `backend/` or `frontend/` and add lines to your own copy of the
   Dockerfile that install it into the image's trust store and point the tools
   at it, for example `uv --system-certs` or `SSL_CERT_FILE` for uv and
   `NODE_EXTRA_CA_CERTS` for Node.
3. Do not commit the certificate or a Dockerfile that depends on it.

If you cannot get builds working, fall back to running Postgres in Docker
(pulling the image uses Docker Desktop's own trust settings) and the backend and
frontend on your host, as in section 7. A proper, shared way to handle this in
the Dockerfiles would be a good follow-up issue.

### Line endings

The `Makefile` and `*.sh` files must keep LF line endings; `.gitattributes`
enforces this. If you see `\r: command not found` from a script, Git converted
it to CRLF, so check your `core.autocrlf` setting and re-checkout the file.

### WSL

If you use WSL 2, clone the repo inside the Linux filesystem (`~/ABM-engine`),
not under `/mnt/c`. File watching and disk performance are much better. Docker
Desktop's WSL integration needs to be switched on for your distro.

## Troubleshooting

### Ports already in use

Symptom: `bind: address already in use` or `ports are not available` for 5432,
8000 or 3000. The usual culprit is a locally installed PostgreSQL on 5432.

Fix: set different host ports in `.env`, then bring the stack up again:

```
DB_PORT=5433
API_PORT=8001
WEB_PORT=3001
```

If you change `API_PORT`, also change `NEXT_PUBLIC_API_BASE_URL` to match, for
example `http://localhost:8001/api`. The browser reads that value, so it must be
the host port.

### `variable is not set` when starting Compose

Symptom: `set POSTGRES_USER in .env` (or `SECRET_KEY`, and so on). You skipped
`cp .env.example .env`, or left a variable empty. Fill it in.

### Postgres rejects the password after you edit `.env`

Postgres only reads `POSTGRES_USER` and `POSTGRES_PASSWORD` the first time it
creates the data volume. Changing them later in `.env` does not change the
existing database. Either put the old values back, or wipe the volume (this
deletes all local data):

```bash
docker compose down -v
docker compose up --build
```

### New dependencies are not picked up

The Python virtualenv and `node_modules` live in named Docker volumes so the
bind mounts do not hide them. After `uv.lock` or `package.json` changes, those
volumes are stale. Run `docker compose down -v` and rebuild, or remove just
`abm-engine_api_venv` / `abm-engine_web_node_modules` with `docker volume rm`.

### `api` keeps restarting

Look at the logs: `docker compose logs api`. Common causes are a missing
`SECRET_KEY`, or `db` not being ready. A migration error after you pulled new
code usually means the database is out of date with someone else's branch; see
the migration section of [backend/README.md](../backend/README.md), including
`reset_local_db`.

### The page loads but shows no data, or the browser cannot reach the API

`NEXT_PUBLIC_API_BASE_URL` must be an address your browser can reach
(`http://localhost:8000/api`), not a Docker service name like `http://api:8000`.
It is also baked in when Next.js starts, so restart the `web` container after
changing it.

### Hot reload does not pick up edits on Windows

File watching on Windows bind mounts is unreliable, so the compose file turns
on polling for the web app (`WATCHPACK_POLLING`). Edits can take a second or two
to show. If Django does not reload, restart it with
`docker compose restart api`.

### Disk or memory trouble on Docker Desktop

The first build downloads several images and installs both toolchains. If a
build is killed or stalls, check that Docker Desktop has a few GB of memory
and free disk, and try `docker system df` before pruning anything.

### Certificate errors during builds or installs

See [Corporate TLS interception](#corporate-tls-interception) above.

### Tests fail on Postgres-only checks

By default tests run on SQLite and skip the Postgres extension tests. To run
them on real Postgres, see "Test settings" in
[backend/README.md](../backend/README.md).
