# 0004. Django + DRF backend, Next.js frontend, PostgreSQL

- Status: Accepted
- Date: 2026-10-06

## Context

Brief §18 suggests Next.js and React for the frontend, Node.js or Python for
the backend, and PostgreSQL (or Supabase) for the database. We need to pick.

The product is data-heavy: companies, research, assessments, contacts and
decisions with relationships between them. It also does a lot of AI and data
work behind the scenes, and needs an admin view for staff and a clean API for
the UI.

## Decision

- Backend: Python with Django and Django REST Framework (DRF).
- Frontend: Next.js with React, talking to the backend over the REST API.
- Database: PostgreSQL.

Django gives us the ORM, migrations, an admin site, validation and a mature
permissions model. DRF gives us serializers and API views on top of that.
Python is also the natural home for the Pydantic schemas in ADR 0003 and for
most LLM and data libraries. PostgreSQL fits relational data and supports JSON
columns for raw provider payloads and AI output.

## Alternatives considered

- **Node.js backend (Express, NestJS)**: one language across the stack, but we
  would assemble ORM, migrations, auth and admin ourselves, and the AI and
  data ecosystem is thinner.
- **FastAPI**: modern and fast, and good with Pydantic, but we would add an
  ORM, migrations, admin and auth separately. Django's batteries matter more
  to us than raw speed.
- **Supabase as the backend (database, auth, APIs)**: Brief §18 mentions it,
  and it speeds up early work, but it moves business logic towards the client
  and database policies, and ties us to a hosted service. See also ADR 0006.
- **Django templates instead of Next.js**: simpler, but a dynamic review and
  approval interface suits a React frontend better.
- **Another database (MySQL, MongoDB)**: no clear gain. Our data is
  relational and Postgres also covers the job queue (ADR 0005).

## Consequences

- Two codebases and two deploys, with an API contract between them.
- Frontend developers need to work against the API rather than the database.
- We get an admin site and migrations almost for free.
- Python and TypeScript both need to be maintained by the team.
- Revisit if the API layer becomes a bottleneck or if the team's skills shift.
