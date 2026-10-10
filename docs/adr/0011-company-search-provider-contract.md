# 0011. Company search provider contract

- Status: Accepted
- Date: 2026-10-10

## Context

Milestone 2 lets a user import companies by searching a licensed data provider
with filters taken from the campaign ICP (issue #66, tracking #55; the import
flow itself is #67 and #68). The real adapters, registry and resilience layer
belong to Milestone 3 (#73 to #82). Before M3 we need a contract both sides
can build against, so the import flow can be written and tested now without a
vendor.

Constraints from the Brief and earlier decisions:

- Brief §4C and §17, ADR 0002: providers are swappable; nothing may assume one
  vendor; no unauthorized LinkedIn scraping.
- Provider rows must go through the same validation, duplicate detection and
  error reporting as manual and CSV rows (#56, #57, #58).
- Credentials and vendor response bodies must never leak into messages, logs
  or stored rows (ADR 0002).

## Decision

We will define a small, pure-Python contract in `backend/apps/imports/providers/`
(no models, no API, no network, no vendor SDK).

- **Request**: `CompanySearchRequest`, a frozen, self-validating dataclass:
  free text `query`; filters `countries` (ISO alpha-2), `industries`,
  `employee_min/max`, `business_model`, `excluded_industries`,
  `excluded_company_types`; cursor paging (`cursor`, `page_size` up to 100);
  `sort`; `limit` (total cap per import, up to 1000).
  `from_campaign_rules(rules_summary)` maps the campaign rules summary, schema
  version 1; another version is refused.
- **Result**: `CompanySearchResult` is one page: `items`, `total_estimate`,
  `next_cursor` (`None` on the last page), `provider_name`, `retrieved_at`,
  `raw_ref` (a provider-side reference, not the payload), `credits_used` and
  `warnings` (a partial page). `ProviderCompany` is the provider-neutral
  company (the issue calls it `CompanyCandidate`). Its `to_company_input()`
  calls the shared `validate_company_input`, so invalid provider data becomes
  field errors on an import row, never an exception.
- **Interface**: `CompanySearchProvider` (a `Protocol`) with `name`,
  `capabilities` and `search(request) -> CompanySearchResult`.
  `capabilities` declares search support, which filters the provider applies
  itself, `max_page_size`, `requires_credentials` and `terms`. `execute_search`
  is the one way to call it: it caps the page size, turns unexpected
  exceptions into `ProviderUnavailable` and checks the result.
- **Errors**: `ProviderError` with `ProviderAuthError`, `ProviderRateLimited`
  (`retry_after`), `ProviderUnavailable`, `ProviderBadRequest` and
  `ProviderQuotaExceeded`. Each has a stable `code` and a fixed message chosen
  by the class. Upstream text is never copied into a message; the vendor
  exception stays in `__cause__`.
- **Registry**: `ProviderRegistry` (explicit `register`, `get`, `list`; no
  import-time side effects) plus a module-level default behind
  `register_provider`, `get_provider` and `list_providers`. M3 #74 may wrap it.
- **Fake provider**: `FakeCompanyProvider`, 25 placeholder companies on
  `example.com` (some Arabic names), all filters, paging, sort, limit and
  errors on demand, for tests and the dev UI.
- **Compliance**: every provider declares `terms` as `licensed_api`,
  `public_data` or `manual`. The registry refuses anything else, and refuses
  scraping-style terms (`scraping`, `crawler` and similar) with the dedicated
  code `terms_forbidden`. Profile URLs (for example LinkedIn company pages)
  are identifiers: stored, normalized and compared, never fetched.
- **Idempotent identity**: `provider_company_key(provider_name, provider_id)`
  returns `"<name>:<id>"`. It maps onto `DataSource.name` and
  `DataSource.provider_reference`. The same key means the same company, checked
  before fuzzy matching (#58, #67); a row without a provider id has no key and
  uses website and name matching.

How paging, limits, cost and partial failure reach the caller: the caller
loops `execute_search` following `next_cursor` until it is `None` or `limit`
companies are collected; `ProviderRateLimited.retry_after` and `retryable` tell
the job when to retry; `ProviderQuotaExceeded` stops the batch; `credits_used`
is summed for cost tracking (#79); `warnings` mark an incomplete page while the
good items still import, and an error mid-way leaves earlier pages imported
(the batch becomes `partial`).

### Candidate providers (to confirm in the M3 spike #73)

This list is a starting point for the licensing review, not a commitment.

| Provider | Access | Notes |
| --- | --- | --- |
| Apollo.io | Official REST API, paid plans | Company search with filters and credits; terms must be checked for storage and reuse. |
| People Data Labs | Official API and licensed datasets | Company search API; bulk licensing available; pay per record. |
| Crunchbase | Official API, licensed | Strong on funding and startups, weaker on regional SMEs. |

Excluded: any service that obtains data by scraping LinkedIn or other sites
without a licence, and LinkedIn itself as a search source (its partner APIs are
not generally available). Such a provider cannot declare an allowed `terms`
value and cannot be registered.

## Alternatives considered

- **Pydantic models (as the issue sketched)**: nice validation, but nothing in
  the backend depends on Pydantic yet and request/result types are internal.
  Frozen dataclasses give the same shape with no new dependency and match
  `CompanyInput`. Revisit if M3 #75 standardizes on Pydantic for all provider
  envelopes.
- **Wait for the M3 framework**: blocks #67 and #68 and risks the import flow
  shaping the adapters by accident. A small contract now, owned by M3 later,
  costs little.
- **A generic `Provider` base for all categories now**: ADR 0002 already says
  one interface per category; this ADR covers company search only.
- **Offset and page numbers instead of an opaque cursor**: simple, but several
  licensed APIs only offer cursors or deep-page limits. A cursor hides that.
- **Free-text error messages from the vendor**: more helpful to debug, but a
  vendor body can contain keys, tokens or personal data. Fixed messages plus
  the exception chain are safer.
- **Trusting adapters to refuse scraping**: a declared `terms` checked at
  registration makes the rule visible and testable, though it still relies on
  honest declaration and code review (#87).

## Consequences

- Import flows (#67, #68) and the dev UI can be built and tested against the
  fake provider with no network or credentials.
- M3 adapters implement one protocol and one error set; the registry, result
  envelope (#75) and resilience layer (#78) can wrap this without changing
  callers.
- Declared `terms` is self-reported. Reviewers must check the claim for each
  real adapter (#73, #87).
- `extra` is capped scalar detail; full raw payloads belong to raw payload
  storage (#80) via `raw_ref`.
- Provider ids are trusted as stable within a provider. If a provider
  re-issues ids, the key rule must change for that adapter.
- Revisit when two real adapters cannot both fit `CompanySearchRequest`, or
  when M3 changes the registry or envelope.
