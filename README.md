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

This repo is at the very start (milestone M0, Foundation and Dev Setup). So far there is only the repo layout and contributor documentation. The backend, frontend and Docker Compose setup are being added through separate issues, so parts of this README describe the target, not what you can run today.

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

> The Docker Compose file arrives in issue #21. Until it is merged, the commands below will not work.

Once it is in, the intended flow is:

```bash
git clone https://github.com/pims-service/ABM-engine.git
cd ABM-engine
cp .env.example .env     # .env.example arrives with the config issues; fill in placeholders
docker compose up
```

That should start the database, API, worker and web app. This README will be updated with ports and URLs when the compose file lands.

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
