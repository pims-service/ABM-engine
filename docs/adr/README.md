# Architecture decision records

Short notes on the significant technical decisions behind ABM Engine: what we
decided, what else we considered and what it costs us. See
[0001](0001-record-architecture-decisions.md) for how we use them and
[template.md](template.md) to write a new one.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-modular-provider-adapters.md) | Modular provider adapters | Accepted |
| [0003](0003-structured-llm-output.md) | Structured LLM output via schemas | Accepted |
| [0004](0004-django-nextjs-postgres-stack.md) | Django + DRF backend, Next.js frontend, PostgreSQL | Accepted |
| [0005](0005-postgres-backed-job-queue.md) | Background jobs on a Postgres-backed queue (Django-Q2) | Accepted |
| [0006](0006-django-jwt-authentication.md) | Authentication with Django JWT (SimpleJWT) | Accepted |
| [0007](0007-keep-history-and-separate-ai-from-human-decisions.md) | Keep history, and store AI recommendations apart from human decisions | Accepted |
| [0008](0008-icp-fit-and-trigger-are-separate.md) | ICP Fit and Trigger are separate concepts | Accepted |
| [0009](0009-data-model-conventions.md) | Data model conventions: tenant column, latest-row lookups, immutable history | Accepted |
| [0010](0010-direct-browser-to-api-with-cors-allowlist.md) | The browser calls the API directly, protected by a CORS allowlist | Accepted |
| [0011](0011-company-search-provider-contract.md) | Company search provider contract | Accepted |

## Adding a new ADR

1. Copy `template.md` to `NNNN-short-title.md` using the next number.
2. Fill it in, keeping it to about a page.
3. Add it to the table above and open a pull request.
4. To change a past decision, write a new ADR and mark the old one
   "Superseded by NNNN".
