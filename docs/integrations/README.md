# Integrations

How ABM Engine talks to outside services, and which ones we are considering.
The design rule is in [ADR 0002](../adr/0002-modular-provider-adapters.md):
every external service sits behind a provider adapter, and no feature depends
on one vendor. Unauthorized LinkedIn scraping is never a dependency (Brief §4).

| Document | What it is for |
| --- | --- |
| [Provider shortlist and licensing review](provider-shortlist.md) | Candidate providers per category, what we could and could not verify, licensing and GDPR/Saudi PDPL notes, a proposed first stack, and a bake-off plan. Research from the spike in issue #73. |
| [Adapter requirements](adapter-requirements.md) | What every adapter must do: auth, rate limits, errors, cost reporting, caching and retention, personal data, idempotency, fixtures and provenance. |
| [Integration framework](framework.md) | The adapter base classes, registry, result envelope, normalized types and errors, and a step-by-step guide to writing an adapter (issues #74 and #75, [ADR 0013](../adr/0013-integration-framework.md)). |
| [Usage, budgets and provider cache](usage-and-cache.md) | `tracked_execute` and `cached_execute`, the `ProviderCall` log, monthly budgets, per-provider `CachePolicy`, raw payload retention and `purge_provider_cache` (issues #79 and #80). |
| [Resilience and provider jobs](resilience.md) | Retries, timeouts, rate limiting and the circuit breaker (`resilient_execute`), and the `provider_task` job base (issues #78 and #86, [ADR 0014](../adr/0014-resilience-and-provider-jobs.md)). |
| [LLM adapter and structured output](llm.md) | Pydantic schemas, `structured_call` with one bounded repair, prompt-injection hygiene, grounding checks and the Anthropic adapter, with the verified documentation links (issue #81). |
| [ADR 0012: first provider selection](../adr/0012-first-provider-selection.md) | Proposed decision, waiting on the owner's commercial choices. |

## Status

The research is done; the decision is not. Nothing here commits the project to a
purchase, and none of it is legal advice. Prices and terms were checked on
2026-10-10 and must be re-checked before committing.

The framework adapters plug into is in place ([guide](framework.md)); the vendor
adapters arrive with the other M3 issues (#76 to #88).
