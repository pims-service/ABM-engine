# Infra

Home for Docker, environment and deployment files.

## Status

Empty for now. The Docker Compose setup (database, API, worker, web) arrives in issue #21. Production deployment and hardening are a later milestone.

## Intended stack

- Docker Compose for local development.
- PostgreSQL for data and for the Django-Q2 job queue. There is no Redis and no Celery.
- Services: `db`, `api`, `worker`, `web`.

## Rules

- Configuration comes from environment variables. Document every variable in `.env.example` with placeholders such as `YOUR_API_KEY`.
- Never commit real credentials. `.env` files are git-ignored.
