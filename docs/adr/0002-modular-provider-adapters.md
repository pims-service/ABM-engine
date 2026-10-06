# 0002. Modular provider adapters

- Status: Accepted
- Date: 2026-10-06

## Context

The product depends on outside services: LLMs, company data, people and
contact data, email enrichment, email verification, CRM (including
GoHighLevel) and outreach platforms. Brief §17 says integrations must be
modular so providers can be replaced, and that we must not hard-code the
product around one data provider. Vendors change prices, coverage and terms,
and different clients will bring their own accounts.

Brief §4 also rules out relying on unauthorized LinkedIn scraping. Whatever
we build must not need it to work.

## Decision

We will put every external service behind a provider adapter.

- One small base interface per category: LLM, company data, people data, email
  enrichment, email verification, CRM, outreach.
- Application code depends on the interface and on our own normalized
  response models, never on a vendor SDK or a vendor's raw payload.
- A registry maps a provider name to its adapter, so choosing or swapping a
  provider is configuration, not a code change in the calling feature.
- Credentials are stored per client, so each client can use their own
  accounts. They are never hard-coded and never written to logs.
- Adapters translate vendor errors, rate limits and missing fields into a
  small common set of errors and "no data" results.
- Unauthorized LinkedIn scraping is not a dependency of any feature. If a
  provider exposes LinkedIn-derived data, it must be one we are allowed to use
  under its own terms. LinkedIn messages are drafted for a human to send.

## Alternatives considered

- **Call one preferred vendor directly**: fastest to build, but it locks us in
  and breaks Brief §17. Replacing the vendor later would touch every feature.
- **A third-party aggregation or integration platform (n8n, Zapier style) as
  the integration layer**: quick to start, but adds a hidden runtime
  dependency, makes testing and versioning harder, and per-client credential
  handling stays outside our control. It can still sit behind an adapter.
- **One giant "provider" interface for everything**: simpler registry, but
  vendors do very different things, so the interface would be mostly optional
  methods. Per-category interfaces are honest about this.
- **Scrape LinkedIn ourselves for people data**: rejected on legal, account
  risk and reliability grounds (Brief §4).

## Consequences

- Providers can be swapped, combined or A/B tested without touching features.
- Tests can use fake adapters, so most of the suite needs no network.
- Some upfront work: defining the interfaces and normalized models before the
  first integration, and keeping adapters in line with them.
- Normalized models can lose vendor-specific detail. We keep the vendor
  reference and, where useful, the raw payload alongside, so nothing is lost.
- Revisit a category's interface if two real adapters cannot both fit it
  cleanly.
