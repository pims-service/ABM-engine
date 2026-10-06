# Docs

Project documentation lives here. If you are new, start with onboarding.

## Start here

- [Developer onboarding](onboarding.md): prerequisites, setup, running the
  stack, Windows notes and troubleshooting.
- [Environment variables and secrets](environment.md): every variable, startup
  validation and the secrets-handling policy.
- [Smoke-test checklist](smoke-test.md): manual check that stack, API and
  frontend work. Notes which parts wait on later issues (worker #25).

## How we work

- [Continuous integration](ci.md): the GitHub Actions jobs, which checks are required and how to run each locally.
- [Conventions](conventions.md): Conventional Commit messages, branch names,
  PR rules and the pre-commit hooks.
- [CONTRIBUTING.md](../CONTRIBUTING.md): ground rules, PR checklist, review
  expectations.

## Why things are the way they are

- [Architecture decision records](adr/README.md): the index of ADRs, plus how
  to add one. Currently covers the adapter pattern, structured LLM output, the
  Django/Next.js/Postgres stack, the Postgres-backed queue, JWT auth, history
  keeping, the ICP fit versus trigger split and data model conventions.
- [Data model and entity relationships](data-model.md): every core object with
  fields and enums, the ER diagram, tenancy and history rules, indexes and
  open questions.

- [Roles and permissions](permissions.md): the role matrix (viewer, reviewer,
  manager, admin, global admin), per-client isolation (404, not 403) and the
  guide every new endpoint must follow.

## Reference for each part of the repo

- [Project README](../README.md): purpose, architecture sketch, layout, V1 scope.
- [infra/README.md](../infra/README.md): Compose services, ports, volumes.
- [backend/README.md](../backend/README.md): Django setup, migrations, seed
  data, tests, code quality.
- [frontend/README.md](../frontend/README.md): Next.js setup and layout.

## Adding docs

Add new docs as Markdown files in this folder and link them from this index.
