# 0007. Keep history, and store AI recommendations apart from human decisions

- Status: Accepted
- Date: 2026-10-06

## Context

Two rules from the Brief shape the data model.

- Brief §19: keep historical research instead of overwriting records, so
  changes can be tracked over time. Companies change, new triggers appear, and
  our own assessments improve.
- Brief §11: human approval is mandatory in V1. The AI gives a recommendation
  but must not start outreach on its own. AI Recommendation is stored
  separately from Human Decision, so we can measure how often people agree or
  disagree with the AI and improve the system.

## Decision

We will model research, assessments and decisions as append-only records.

- Company research, signals/triggers, ICP assessments and AI recommendations
  are inserted as new rows with timestamps. A new run adds a record. It does
  not update or delete the old one. "Current" is the latest record, found by
  query or a pointer, not by overwriting.
- Each AI result records where it came from: the data source, the model and
  the prompt and schema versions (see ADR 0003).
- An AI recommendation and a human decision are separate records. The human
  decision references the recommendation it was made against, and carries the
  user, the time and an optional reason. A human decision never edits the AI
  record, and the AI never writes a human decision.
- Outreach and CRM push only start from an approved human decision.
- Corrections are made by adding a newer record. Deletion is limited to cases
  such as privacy requests, handled deliberately.

## Alternatives considered

- **Update rows in place**: simplest schema and queries, but old research and
  the AI's earlier view are lost, so we cannot see changes or measure
  agreement over time.
- **Single table with a status field holding both AI suggestion and human
  choice**: fewer tables, but the AI's original answer gets overwritten when a
  person overrides it, which defeats Brief §11.
- **Generic audit-log or history library on top of mutable rows**: records
  changes, but history is harder to query and reason about than first-class
  records, and it does not model "AI said X, human decided Y" cleanly.
- **Event sourcing for everything**: powerful but far more machinery than V1
  needs. We use append-only only where it matters.

## Consequences

- We can show how an account's picture changed and compare AI to humans, which
  feeds prompt and rule improvements.
- Reads must pick the latest record, so queries and indexes need care.
- Storage grows with every re-run. Acceptable at V1 volume, to be watched.
- The UI must make clear which result is current and which is history.
- Personal data in old records needs a deliberate retention and deletion
  approach when we handle those requests.
- Revisit if storage or query cost becomes a problem. Archiving old records
  would then be a better fix than overwriting.
