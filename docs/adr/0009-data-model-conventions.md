# 0009. Data model conventions: tenant column, latest-row lookups, immutable history

- Status: Accepted (implemented in issues #39 to #46, #52 and #54)
- Date: 2026-10-06

## Context

ADR 0007 says to keep history and ADR 0008 says ICP Fit and Trigger are
separate. Writing the data model (see [data-model.md](../data-model.md), issue
#38) needs three more choices that every model issue (#39 to #44) will repeat,
so we make them once.

- Brief §3 and the M1 exit criteria: several clients with different ICP rules,
  and no data leaking between them.
- Brief §19: keep historical research. We need a way to say which record is
  "current" without overwriting.
- Brief §5, §7, §22: evidence has a source and a date, and nothing is invented.

## Decision

We will follow these conventions.

1. **Tenancy**: every tenant-owned table has a direct `client_id` foreign key,
   copied from its parent when the row is created. Scoped managers filter on
   it, and a test checks that `client_id` matches along every foreign key. A
   company is one row per campaign. We never share a company record between
   clients, or dedupe across them.
2. **"Current" without a flag**: append-only tables have no `is_current`
   column. The current row is the latest by timestamp (`researched_at`,
   `decided_at` or `created_at`, tie-broken by id), found through one manager
   method per model and a matching descending index. The one exception is
   `Campaign.current_profile`, a pointer, because choosing the live rules is
   an explicit act.
3. **Immutable history**: research, assessments, recommendations, decisions,
   angles, signals, activities, audit entries and campaign profile versions are
   inserted and never updated or deleted by application code. Corrections are
   new rows, optionally with a `supersedes_id`. Messages keep immutable text,
   and only workflow status moves forward.
4. **Trigger state is computed**: a signal has `event_date`, `detected_at` and
   `expires_at`, and a required source. There is no stored "active" flag or
   Yes/No. A trigger is Yes when a fresh, unsuperseded signal exists.
5. **Enums are database-checked**, and lists of short strings use Postgres
   arrays.
6. **We archive, we do not cascade**: foreign keys are PROTECT, records are
   hidden with `archived_at`, and hard deletion is a deliberate admin procedure
   for privacy requests only.

## Alternatives considered

- **Join through campaign for scoping (no `client_id` on children)**: no
  duplicated column, but every scoped query needs joins, and one forgotten
  join is a data leak. We accept the duplication and test it.
- **Row-level security in Postgres**: strong, but it adds database role and
  session plumbing that does not fit Django-Q2 and JWT auth well yet. Revisit
  if we add other consumers of the database.
- **One global Company shared across campaigns and clients**: avoids repeat
  research, but leaks one client's context into another and ties assessments
  to a shared mutable record. Rejected.
- **`is_current` flag on history rows**: quick to read, but needs a second write
  on the old row, which breaks append-only and can race. Rejected.
- **Stored `active` flag or Yes/No on signals**: needs a job to keep it right
  and is stale in between. Rejected for evaluation at read time.

## Consequences

- A scoped query is one `WHERE client_id IN (...)`, and isolation is testable.
- Latest-per-company reads need the right indexes and `DISTINCT ON` or lateral
  queries. Fine at V1 volume.
- Writers must set `client_id` through shared code, not by hand. Mismatches are
  caught by tests rather than the database.
- Storage grows with every re-run (as ADR 0007 notes).
- Per-type freshness windows are still to be defined (M5). Until then
  `expires_at` can be null, which counts as fresh.
- Revisit if we need cross-client reporting, or if Postgres row-level security
  becomes practical.
