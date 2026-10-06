# ABM Engine

An internal web app that automates the manual research behind Account-Based Marketing (ABM). Working name: Growviah ABM Engine.

The manual process today is: find a company, research it, decide whether it fits, look for a reason to reach out now, check the commercial team, find the right decision-maker, pick an angle, approve or reject, and prepare outreach. This project automates the research and analysis parts. A person keeps the final say.

The goal is to let someone review an account in roughly 30 to 60 seconds instead of researching it across several sources by hand.

## What it is (and is not)

ABM Engine is the decision layer between raw company and contact data and outreach. Its value is:

- campaign-specific ICP rules
- trigger rules, evaluated separately from ICP
- buyer-selection logic
- evidence-backed research with a visible source and date
- outreach angles grounded in that evidence
- human approval (ADD / HOLD / SKIP)

Two ideas drive the whole product:

- **ICP fit** (Strong / Medium / Weak) asks whether a company is a good prospect at all.
- **Trigger** (Yes / No) asks whether there is a reason to contact them right now.

A company does not need a trigger to qualify. Strong ICP with no trigger is still eligible.

It is not another Clay, Sales Navigator, CRM or outreach platform. See the out-of-scope list below.

## Status

This repo is at the start (milestone M0, Foundation and Dev Setup). The Django and Next.js skeletons and the Docker Compose dev environment exist; product features and the Django-Q2 worker are still being added through separate issues, so parts of this README describe the target.

## Architecture (planned)

```
 Browser
    |
    v
 Next.js + TypeScript  (frontend/)
    |  REST/JSON, JWT
    v
 Django + Django REST Framework  (backend/)
    |                 \
    |                  \  enqueue jobs
    v                   v
 PostgreSQL  <------  Django-Q2 workers
 (data + job queue)   (research, analysis, imports)
                          |
                          v
                  provider adapters
              (licensed data providers, LLM)
```

Decisions already made:

- Frontend: Next.js with TypeScript.
- Backend: Django with Django REST Framework. Auth uses JWT (SimpleJWT).
- Database: PostgreSQL.
- Background work: Django-Q2 using PostgreSQL as the queue. No Redis, no Celery.
- Local environment: Docker Compose.
- Data providers and LLMs sit behind modular adapters so a provider can be swapped without rebuilding the product.
- Where AI output drives application logic, it uses structured schemas rather than free-form text.
- Research history is kept, not overwritten, so changes can be tracked over time.

## Quick start

Requires Docker with Compose v2.

```bash
git clone https://github.com/pims-service/ABM-engine.git
cd ABM-engine
cp .env.example .env     # then replace the YOUR_* placeholders with local values
docker compose up --build
```

This starts three services:

| Service | URL / port | Notes |
| --- | --- | --- |
| `db` | `localhost:5432` | PostgreSQL 16, data in the `pgdata` named volume |
| `api` | http://localhost:8000/api/v1/ | Django dev server; runs migrations on start |
| `web` | http://localhost:3000 | Next.js dev server |

Source in `backend/` and `frontend/` is bind-mounted, so edits hot-reload. Data survives `docker compose down`; use `docker compose down -v` to wipe it. If a default host port is taken, set `DB_PORT`, `API_PORT` or `WEB_PORT` in `.env` (and update `NEXT_PUBLIC_API_BASE_URL` if you move the API). The `worker` service runs the Django-Q2 cluster (`python manage.py qcluster`) against the same Postgres.

`make up`, `make down`, `make logs`, `make migrate`, `make test` and `make shell` wrap the common commands. See [infra/README.md](infra/README.md) for details.

New to the project? Follow [docs/onboarding.md](docs/onboarding.md) (setup, Windows notes, troubleshooting) and then the [smoke-test checklist](docs/smoke-test.md). The reasoning behind the stack is in the [architecture decision records](docs/adr/README.md), and commit, branch and PR rules are in [docs/conventions.md](docs/conventions.md).

## Repository layout

```
backend/    Django + DRF API, Django-Q2 tasks (added by backend issues)
frontend/   Next.js + TypeScript app (added by frontend issues)
infra/      Docker, deployment and environment notes (see infra/README.md)
docs/       Project documentation and decision records (see docs/README.md)
.github/    Pull request and issue templates
```

`backend/` and `frontend/` do not exist yet on every branch; they are created by their own setup issues.

## Out of scope for V1

We are deliberately not building:

- a complete CRM
- email sending infrastructure
- LinkedIn automation
- a dialer
- a complex analytics suite
- an intent-data network
- website visitor identification
- a large multi-agent framework
- dozens of integrations

Existing tools already cover these. V1 is ABM research, qualification, trigger detection, buyer selection and personalization. We also do not depend on unauthorized LinkedIn scraping; company and people data should come from official APIs or licensed providers.

If a change looks like it belongs on this list, raise it before building it.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for branch names, commit style and the PR checklist. Never commit real keys or credentials.
