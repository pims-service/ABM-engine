# 0012. First provider selection

- Status: Proposed
- Date: 2026-10-10

This is a draft. It needs the project owner's commercial decision before it can
be Accepted. Nothing in it is a purchase recommendation or an instruction to
sign anything.

## Context

Brief §17 asks for replaceable providers across several categories, and Brief §4
forbids relying on unauthorized LinkedIn scraping. [ADR 0002](0002-modular-provider-adapters.md)
set the adapter pattern. Before building the first real adapters (issues #82 to
#85) we need to know which licensed providers to start with. The spike (#73)
produced [the provider shortlist](../integrations/provider-shortlist.md) and
[adapter requirements](../integrations/adapter-requirements.md).

Key findings from the research (2026-10-10):

- No vendor publishes Saudi Arabia coverage or Arabic data quality, so coverage
  for the Saudi and GCC use case is unproven for every option.
- Department headcount and 3, 6 and 12 month growth (Brief §5) are available in
  documentation from PDL (some as a paid add-on) and probably Coresignal; field
  names for Coresignal are unverified.
- Apollo's API terms restrict integrating the API into "your product or
  services" without approval, which conflicts with an adapter-based product.
- Storage, caching, AI processing and CRM export rights were not confirmed for
  any vendor from primary terms.
- The Saudi PDPL has a consent rule for advertising by personal means such as
  email; its application to B2B outreach needs Saudi legal advice.

## Decision

We propose the following, subject to the owner's answers below and a bake-off on
free tiers.

- **Company data:** People Data Labs first, with Coresignal as the challenger in
  the bake-off. Wathq (Saudi Ministry of Commerce) is evaluated as a second source
  for legal identity.
- **People data:** PDL person search; fallback Coresignal employee data, or
  Apollo only with written permission.
- **Email enrichment:** Hunter; fallback PDL email fields or another finder after
  a provenance check.
- **Email verification:** ZeroBounce; fallback Bouncer or Kickbox.
- **LLM:** Anthropic Claude through the structured-output adapter; fallback
  OpenAI or Gemini.
- **CRM:** GoHighLevel using a Private Integration Token; fallback HubSpot or
  CSV export. No outreach sender in V1 (Brief §21).

We will not sign a contract, buy a plan or load production data until the owner
has decided, and the final primary and fallback per category will be recorded
here by changing the status to Accepted (or by a superseding ADR).

## Questions for the owner

1. **Budget.** What is the monthly and per-500-company budget for data, email
   enrichment, verification and LLM calls? Is a hard credit cap per vendor
   acceptable during the bake-off?
2. **Contract appetite.** Is the project willing to take an annual or enterprise
   contract (Cognism, Crunchbase, higher PDL tiers), or only month-to-month and
   pay-as-you-go until accuracy is proven?
3. **Regions.** Is Saudi Arabia the only market for V1, or also UAE and the rest
   of the GCC? Any EU or UK targets (which brings GDPR in)?
4. **Clients.** Will the engine serve only Growviah, or also external clients
   with their own accounts (affects "internal use only" licences and per-client
   credentials)?
5. **Legal review.** Who reviews the vendor terms and the Saudi PDPL position,
   and before which milestone? Is B2B cold email to Saudi contacts in scope for
   V1, or only research and drafts?
6. **Data handling.** Is sending personal data to a US-hosted LLM or vendor
   acceptable, or do we need regional hosting or contractual transfer
   safeguards?
7. **GoHighLevel.** Which plan and sub-account will be used, are Private
   Integrations enabled, and what is the location's duplicate-contact setting?
8. **LinkedIn-derived data.** Is the team comfortable with a vendor whose public
   web data may include LinkedIn-derived fields if the vendor warrants lawful
   collection, or should such vendors be excluded?
9. **Bake-off.** Who owns the test list, and what accuracy thresholds count as
   a pass (the shortlist proposes starting values)?

## Alternatives considered

- **Pick Apollo as primary.** The largest free tier and broad endpoints, but the
  API terms clause on integrating into a product, and weak reported Saudi
  coverage. Possible later with written permission.
- **Start with a premium regional vendor (Cognism or similar).** Stated Middle
  East effort, but quote-only pricing, platform fees and annual contracts make it
  a poor first step before we know our needs.
- **Rely on LinkedIn-based tools.** Rejected: Brief §4, vendor shutdowns and
  lawsuits, no official API for this use.
- **Choose all providers now without a bake-off.** Faster, but there is no Saudi
  evidence to support it.
- **Build on an aggregator such as Clay or n8n.** Already rejected as the
  integration layer in ADR 0002.

## Consequences

- The first adapters can start on the Proposed choices, because the adapter
  interface keeps them replaceable. If the owner chooses differently, the cost is
  a different adapter module, not a redesign.
- The bake-off costs some time and a few free-tier credits, and delays a final
  commitment.
- Until licences are read, adapters use the conservative retention profile in
  [adapter requirements](../integrations/adapter-requirements.md#9-caching-and-raw-payload-retention-80).
- Revisit this ADR when the bake-off results are in, when any vendor changes its
  terms or pricing, or when the target regions change.
