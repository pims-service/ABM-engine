# 0013. Integration framework: one adapter model for every category

- Status: Accepted
- Date: 2026-10-11

## Context

ADR 0002 says every external service sits behind a provider adapter, and ADR
0011 defined the first concrete contract (company search, issue #66) for the
M2 import flow. Milestone 3 needs the same idea for all nine Brief section 17
categories: LLM, company data, people and contact data, email enrichment,
email verification, CRM (including GoHighLevel), outreach platforms,
database and storage, and automation and background jobs. Issues #74 (base
interface and registry) and #75 (result envelope, normalized types, errors)
are the foundation for #76 to #88.

Constraints: providers stay replaceable (Brief section 17), source and date
are stored for important evidence (Brief section 5), no fabricated data
(Brief section 22), no scraping (Brief section 4), secrets never reach logs
or responses (ADR 0002), and the #66 public API and tests must keep working.

## Decision

We will add a pure-Python package `backend/apps/integrations/framework/` (no
models, migrations, endpoints, network or vendor SDK) and build the rest of M3
on it.

- **Adapter model**: an `Adapter` ABC plus one base class per category with
  typed operations (company search and enrichment, find people, find email,
  verify email, structured LLM generation, idempotent CRM push, outreach
  export, storage, automation). Each adapter declares `key`, `display_name`,
  `version`, `terms`, `capabilities` and a JSON-schema-like `config_schema`
  that names non-secret settings and credential names, never values. An
  operation an adapter does not support is left out of its capabilities and
  refused by `execute`.
- **Capabilities** are a closed per-category vocabulary, checked at
  registration; `supports(adapter, feature)` and the registry's `supporting`
  let callers negotiate instead of branching on vendors. Outreach has only
  `export` in V1, so a sending adapter cannot register.
- **Registry**: `AdapterRegistry`, keyed by `(category, key)`, explicit
  registration, no import-time side effects, `resolve(ConnectionSpec)` builds
  an instance from a small `ConnectionSpec` (category, key, config,
  credentials as `SecretStr`). The credentials/settings models of #76 construct
  the spec; the framework does not depend on them.
- **Envelope**: `AdapterResult[T]` with `Provenance` (provider, category,
  source URL or reference, `retrieved_at` timezone aware, `evidence_date`,
  request id, terms, cache hit, raw payload pointer) and `Usage` (credits,
  Decimal cost and currency, tokens, latency). Evidence-bearing data without a
  source in the provenance is refused at construction.
- **Normalized types** shared across categories: `NormalizedCompany`
  (extending `ProviderCompany` semantics with department headcounts, 3/6/12
  month Decimal percent changes, funding and signals), `NormalizedPerson`,
  `SignalCandidate` (Brief section 7 types), `EmailVerification`,
  `EmailFinding`, `NormalizedJobPosting`. `None` means unknown. Bad provider
  values become `None`; missing required parts raise `EnvelopeError`.
- **Errors**: the #66 family and codes are kept; we add
  `ProviderNotConfigured`, `ProviderNotFound`, `ProviderConflict`,
  `ProviderTimeout`, `ProviderUpstreamError` and `ProviderInvalidOutput`, each
  with a stable `code`, `retryable`, `retry_after` and a safe `as_dict()`,
  and `map_http_status`.
- **Compatibility with #66**: the error classes and the terms check move into
  the framework and `apps.imports.providers` re-exports them (same classes,
  codes, messages), so existing imports and tests are unchanged. The #66
  `CompanySearchProvider` contract stays as is; `bridge.py` adapts a
  `CompanyDataAdapter` to it so #67 keeps using `execute_search`.
- **Boundary**: concrete adapters live in `apps/integrations/adapters/<vendor>`
  and only that package may import them; a test scans app code and fails
  otherwise. A second test keeps the framework free of Django DB, network and
  vendor SDK imports.
- **Fakes and contract tests**: one in-memory fake per category and reusable
  contract mixins in `tests/adapter_contract.py` that every real adapter
  subclasses.

## Alternatives considered

- **Keep only the #66 company-specific contract and copy it per category**:
  no shared envelope, errors or registry, so provenance and error handling
  would drift. Rejected.
- **Build the framework without moving the #66 errors**: leaves two error
  families that callers must translate. We moved them and re-export instead;
  the cost is a one-line change in three #66 modules.
- **Pydantic models for the envelope (as the issue sketched)**: Pydantic is
  not a dependency yet and `uv.lock` is shared with other branches. Frozen
  dataclasses with explicit cleaning give the same guarantees and match #66.
  `StructuredSchema` takes a `validate` callable, so Pydantic models plug in
  for LLM output (`Model.model_validate`) when #81 adds the dependency.
  Revisit if the envelope should be serialized by a framework.
- **Protocols only, no base class**: more flexible but adapters then repeat
  config validation, secret handling and provenance stamping. An ABC with
  class-level declarations lets the registry inspect an adapter without
  instantiating it.
- **A registry of instances**: instances need credentials, which are per client
  and per call. The registry holds classes and builds instances from a
  `ConnectionSpec`.
- **Auto-discovery by scanning the adapters package**: import-time side
  effects and hidden failures. Explicit registration is boring and testable.
- **One generic `call(operation, **kwargs)` entry point**: loses typing.
  Typed methods plus `execute` keep both.

## Consequences

- Adding an adapter is one module plus one registration line, and it inherits
  config validation, secret hiding, provenance stamping and the contract
  tests.
- Callers depend on the envelope and normalized types only; research (M4)
  can store provenance as `DataSource` rows directly.
- The shared HTTP layer (#78), settings (#76, #77), cost tracking (#79) and
  jobs (#86) read `retryable`, `retry_after`, `usage` and `Provenance` instead
  of vendor details.
- The closed feature vocabulary must grow with new needs: adding a feature is
  a one-line change in `categories.py` plus its operation.
- The #66 bridge does not negotiate search filters yet; callers re-check
  filters on our side as ADR 0011 says.
- Revisit when Pydantic enters the backend (#81), and when a category needs an
  operation the closed vocabulary does not cover.
