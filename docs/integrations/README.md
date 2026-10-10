# Integrations

How ABM Engine talks to outside services, and which ones we are considering.
The design rule is in [ADR 0002](../adr/0002-modular-provider-adapters.md):
every external service sits behind a provider adapter, and no feature depends
on one vendor. Unauthorized LinkedIn scraping is never a dependency (Brief §4).

| Document | What it is for |
| --- | --- |
| [Provider shortlist and licensing review](provider-shortlist.md) | Candidate providers per category, what we could and could not verify, licensing and GDPR/Saudi PDPL notes, a proposed first stack, and a bake-off plan. Research from the spike in issue #73. |
| [Adapter requirements](adapter-requirements.md) | What every adapter must do: auth, rate limits, errors, cost reporting, caching and retention, personal data, idempotency, fixtures and provenance. |
| [ADR 0012: first provider selection](../adr/0012-first-provider-selection.md) | Proposed decision, waiting on the owner's commercial choices. |

## Status

The research is done; the decision is not. Nothing here commits the project to a
purchase, and none of it is legal advice. Prices and terms were checked on
2026-10-10 and must be re-checked before committing.

Adapter code and the step-by-step "add a provider" guide arrive with the other
M3 issues (#74 to #88).
