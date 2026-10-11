# Adapter requirements

What every provider adapter must meet, whichever vendor it wraps. The
requirements come from the M3 issues (#74 to #88), the Brief (§4, §5, §12, §17,
§22) and the provider research in [provider-shortlist.md](provider-shortlist.md).
They build on [ADR 0002](../adr/0002-modular-provider-adapters.md) and
[ADR 0003](../adr/0003-structured-llm-output.md).

This is a requirements list for the people building the adapters. It describes
what the code must do; the code itself lives in later issues. Where a
requirement depends on a vendor's licence, the per-provider notes in the
shortlist and the signed contract win over this page.

Keywords: **must** is required, **should** is expected unless there is a
written reason.

## 1. Scope and boundaries (#74)

- An adapter implements exactly one category interface: `LLMProvider`,
  `CompanyDataProvider`, `PeopleDataProvider`, `EmailEnrichmentProvider`,
  `EmailVerificationProvider`, `CRMProvider` or `OutreachProvider`.
- It declares its category, name, version, supported capabilities and required
  config keys. Unsupported capabilities are declared, not faked. For example, a
  company provider with no 12-month growth says so (#82), so research (M4)
  records "not available" instead of guessing.
- Application code reaches an adapter only through the registry. A CI check
  fails if code outside the integrations package imports a vendor module (#74,
  #87).
- Every category has a mock adapter. A new adapter needs only a new module plus
  registration.
- No adapter may rely on scraping, a logged-in LinkedIn session, or any access
  the vendor's terms do not grant (Brief §4). A new adapter's pull request must
  state the vendor's data source type and link the licence terms that were
  reviewed.

## 2. Authentication and secrets (#76)

- Credentials come from integration settings (encrypted, per client with a
  global default). Adapters never read keys from code, files in the repo or
  environment variables that the settings do not own.
- Declare the config keys and mark which are secret. The settings UI builds its
  form from this (#77). Examples in docs and tests use placeholders such as
  `YOUR_API_KEY`.
- Support the vendor's auth model honestly. From the research:
  - Hunter: key via query string, `X-API-KEY` or Bearer header. **Must** use the
    header, never the query string, so keys do not reach URL logs.
  - GoHighLevel: Private Integration Token (static, scoped, 90-day rotation
    recommended with a 7-day overlap) or OAuth (about 24-hour access token plus
    refresh token). V1 uses the token plus `locationId`; the adapter must make
    the token swappable without a code change and treat rotation as normal.
  - Others: API key in a header. OAuth flows need a documented refresh path and
    must never log tokens.
- `AuthError` is raised on 401 and 403 and **never retried** (#78).
- Every adapter implements a lightweight health check for Test Connection
  (#77) that costs no credits where the vendor allows it. Where the only
  available call spends credits, say so in the adapter metadata and let the UI
  warn.
- Secrets never appear in logs, exceptions, `ProviderCall` rows, raw payloads or
  fixtures (#76, #79, #87). Redact at the HTTP client layer.

## 3. HTTP behaviour: timeouts, retries and rate limits (#78)

- Use the shared HTTP client wrapper, not a vendor SDK that opens its own
  connections. It sets connect and read timeouts and a descriptive
  `User-Agent`. Adapters **must** allow a per-operation timeout override, since
  verification and LLM calls are slower than lookups.
- Calls go only to allow-listed vendor hosts, declared in adapter metadata
  (SSRF protection, #87). A URL that comes from provider data is never fetched.
- Retries: exponential backoff with jitter for transient errors (5xx, timeouts,
  connection resets) and 429; honour `Retry-After`; never retry auth or
  validation errors; retry only operations that are safe to repeat (see
  idempotency below).
- Rate limits are declared in adapter metadata (requests per second and per
  minute and any daily cap) and enforced by the shared limiter across workers.
  Known vendor numbers (verify before use, they change):

  | Vendor | Limit to configure (see shortlist for source) |
  | --- | --- |
  | PDL company enrich | 10/min free, 1,000/min paid |
  | Coresignal | 5 req/s on trial and entry plans, more on higher plans |
  | Hunter | Finder and Domain Search 15 req/s and 500/min; Verifier 10 req/s and 300/min |
  | Apollo | Plan-dependent per-minute, hourly and daily caps; sends `retry-after` and usage headers |
  | GoHighLevel | 100 per 10 s and 200,000 per day per app per location; sandbox 25 per 10 s and 10,000 per day |

- Where a vendor returns remaining-quota headers (Apollo, GoHighLevel), the
  adapter should surface them to the limiter so it slows down before a 429.
  GoHighLevel's page does not document 429 behaviour, so treat any 429 as
  `RateLimited` and back off conservatively.
- A circuit breaker opens after repeated failures and surfaces
  `ProviderUnavailable` (#78).

## 4. Pagination and result limits

- Hide vendor pagination (cursor, page number, scroll token) behind an
  iterator or a `limit` plus `next_cursor` in our own types.
- Every search operation takes a **mandatory maximum result count** with a safe
  default. Cost and credit exposure must be bounded by the caller. Examples:
  Coresignal search preview costs credits per page; PDL charges per matched
  record; Apollo people search is free but enrichment costs credits (#83).
- Pagination must be resumable and deterministic enough that a retry does not
  double charge. Document whether each page costs credits.
- Search is never "fetch everything" by default.

## 5. Error mapping (#75)

Map every vendor failure to the typed hierarchy. Retry behaviour is part of the
type.

| Type | Typical vendor signal | Retry |
| --- | --- | --- |
| `AuthError` | 401, 403, invalid key | No |
| `RateLimited` (with retry-after) | 429, vendor "limit reached" | Yes, after the wait |
| `NotFound` | 404, PDL 404 (not billed), "no match" | No. It is a normal result, not a fault |
| `QuotaExceeded` | Out of credits, plan cap, budget block (#79) | No, until the owner acts |
| `ProviderUnavailable` | 5xx, timeouts, breaker open | Yes, with backoff |
| `InvalidRequest` | 400, 422, bad parameters | No |
| `InvalidModelOutput` (LLM only, #81) | Schema validation failure after one repair | No |

- Never leak the vendor payload or request headers into an error message.
- Distinguish "no match" from "match with missing fields". Both are valid
  outcomes; neither is guessed.
- The verification and enrichment adapters (#84) treat provider timeouts and
  quota errors as errors, not as `unknown` results.

## 6. Normalized output and missing data (#75)

- Return only the normalized Pydantic models. Map vendor fields to the company,
  headcount-by-department-and-period, people, job posting, email and
  verification models; keep unmapped fields in the raw payload (#82).
- **Missing is `None`. Never default, estimate or infer in an adapter.** An
  absent employee count is `None` with a warning. Brief §22 forbids fabricated
  data.
- Some vendors compute values (PDL growth rates are decimals, for example `0.2`
  means 20%; employee counts from PDL come from resume data). The adapter
  normalizes units and documents the vendor's meaning and any caveat in the
  `warnings` field.
- Keep the original value alongside any normalization. For people, keep the
  original title untouched next to the normalized one (#83).
- Verification status uses the shared enum (`valid`, `invalid`, `risky`,
  `unknown`, `catch_all`) with the vendor reason (#84). Map from vendor values
  such as Hunter `accept_all` and `webmail`, Kickbox `deliverable` and `risky`,
  ZeroBounce sub-statuses. **Never upgrade `unknown` to `valid`.** If a vendor
  has no catch-all concept, say so in metadata.
- Do not turn a vendor's confidence or "likelihood" into our scores. Pass it as
  provider confidence, labelled as such.

## 7. Provenance and evidence (Brief §5, #75)

Brief §5 says to store source and date for important evidence. Every
`AdapterResult` must carry these fields, and validation rejects a result without
them.

| Field | Rule |
| --- | --- |
| `provider` | Registry key and adapter version |
| `retrieved_at` | Time of the actual vendor call in UTC. A cache hit keeps the **original** retrieval time and flags `from_cache` |
| `source_url` | A real URL for the evidence when the vendor gives one (job posting page, news article, funding announcement). `None` if there is none. Never construct or guess a URL |
| `source_type` | For example `vendor_database`, `job_posting`, `news`, `registry` (Wathq-style). Lets the UI say where a claim came from |
| `vendor_record_date` | The vendor's own updated or last-seen date if provided (separate from `retrieved_at`) |
| `raw_payload_ref` | Link to the stored raw payload (see section 9), or `None` if the licence forbids keeping it |
| `quality` | Normalized quality or confidence flag, and any `warnings` |
| `cost` | Units or credits consumed for this call |

Rules that follow:

- Evidence dates matter to trigger freshness (Brief §7). The date that decides
  whether a trigger is current is the **event date**, which is different from
  `retrieved_at`. Adapters supply the event date only when the vendor does. A
  retrieved-today job posting with no posted date has no event date.
- Headcount growth is "as of" the vendor's computation date. Adapters must pass
  that date, since PDL and Coresignal refresh on different cycles, and a figure
  of unknown age must be marked so.
- Evidence in M4 cites the adapter result, never just a company-level
  statement. ADR 0003 requires claims to cite a source.

## 8. Cost and credit reporting (#79)

- Every call reports units used and an estimated cost, from the vendor's
  response where it gives one and from adapter metadata otherwise. Credit
  models differ (per matched record, per found email, per page, per row
  returned, free when nothing is found, zero for unknowns) so the adapter
  declares its own rules and the tests check them.
- Record "billable" separately from "attempted". A PDL 404, a Lusha empty
  result and a ZeroBounce unknown cost nothing; the log must show that.
- Instrument through the adapter base class so a call cannot skip logging.
- Expose a credit balance or usage check where the vendor has one (ZeroBounce
  credit-balance endpoint, Lusha usage API, Hunter account endpoint) so the UI
  and budgets can use it. Rate limits on those endpoints are stricter than on
  data endpoints (Lusha: 5 per minute). Document them.
- Honour soft and hard budgets (`QuotaExceeded`) before making a call, not
  after.
- Estimated cost is an estimate. Prices are configuration in integration
  settings, never constants in code, because vendors change them.

## 9. Caching and raw payload retention (#80)

The rule is: **what we may keep depends on the vendor's licence and on whether
the payload contains personal data.** Adapters declare a retention profile in
metadata, and the caching layer enforces it.

| Data type | Cache | Raw payload retention |
| --- | --- | --- |
| Company firmographics, headcount, funding | Yes, with a TTL per operation (suggest weeks for firmographics, shorter for jobs and news) | Keep, per licence, for audit and re-parsing |
| People and contact records (personal data) | Yes, short TTL | Keep only as long as the retention setting allows; default short; delete on request and on provider opt-out notice |
| Email addresses from enrichment | Yes | Same as people; treat as personal data |
| Verification results | Short TTL (deliverability changes) | Same; do not keep raw beyond need |
| LLM prompts and responses | Cache only deterministic structured calls | Keep prompt name and version, model, token usage; keep full text only if no personal data or per retention policy |
| News and job postings (public text) | Yes | Keep with `source_url` and event date |

- If the vendor's terms prohibit storing or reusing a data family, the adapter
  sets `raw_payload_allowed = False` and the cache keeps only normalized fields
  the licence allows. We have not verified storage rights for most vendors
  (see the shortlist); **default to the conservative profile until the contract
  is read**.
- `force_refresh` creates a new record and keeps the old one (Brief §19,
  ADR 0007). A cache hit makes no provider call and costs nothing.
- Support the vendor's deletion and opt-out flow: when a vendor or a person asks
  to remove data (PDL provides a Privacy Center and API-side removal), the
  retention job must be able to purge by vendor record ID. Adapters must keep
  and expose the vendor record ID for this.
- Raw payloads are stored without secrets and with request headers stripped.
- Derived AI processing: before sending vendor data to an LLM, check the
  adapter's `ai_processing_allowed` flag (default unknown, which means ask the
  owner). The shortlist lists which licences have been checked, which is none
  so far.

## 10. Personal data handling

Applies to people, email enrichment, email verification, CRM and LLM adapters.
Context in the shortlist's GDPR and Saudi PDPL section.

- Enrich and look up contact details **only for accounts a human approved**
  (Brief §12). The adapter does not enforce the approval; the caller does, and
  tests in M8 assert it. The adapter must still refuse a bulk call above a
  configured size.
- Request the minimum fields. Do not request mobile numbers or personal emails
  unless the feature needs them and the vendor charges extra (Apollo mobile
  adds credits).
- Mask personal data in logs, `ProviderCall` rows and error text (#79, #87).
  Log IDs and counts, not names and emails.
- Do not send personal data to an LLM unless the prompt truly needs it. Prefer
  role and company context. Cross-border transfer rules for Saudi data apply
  to vendors and LLM providers alike (see the shortlist).
- Keep a per-record source so a data subject request ("where did you get my
  email?") can be answered with vendor and date.
- Tests and fixtures never contain real personal data (#82, #83, #87).

## 11. Idempotency (#85, #84, #78)

- **Read operations** are safe to retry. A retry must not bill twice where the
  vendor allows duplicates to be free (Hunter: repeated searches count once per
  month, per its pricing page); otherwise rely on the cache.
- **Write operations (CRM and outreach)** must be idempotent from our side. For
  GoHighLevel there is no documented idempotency key. Use `POST
  /contacts/upsert`, which follows the location's duplicate setting, and
  store the remote contact ID and our external key to avoid duplicates. Check
  how the location is configured (email, phone, or both) and document it as a
  setup requirement. Treat a create-versus-update flag in the response as data
  to record.
- **Dry run** returns the exact payload that would be sent, with no network
  call (#85). Dry-run output must be built by the same code path as the real
  payload.
- **Nothing is sent without an explicit call from the caller.** An adapter never
  decides to push or send (#85, Brief §21).
- Non-idempotent paid calls (for example Apollo reveals) must not be retried
  automatically after an ambiguous failure (timeout after send) without
  checking whether the first call succeeded.

## 12. LLM adapter specifics (#81)

- `generate_structured(prompt, response_model, ...)` returns a validated model,
  token usage and model name. Use native structured output where the vendor has
  it (Claude `output_config.format`, OpenAI `json_schema` strict, Gemini JSON
  Schema), but our own Pydantic validation stays the source of truth (ADR 0003).
- Vendor schema subsets differ (for example Claude rejects numeric and string
  length constraints and recursive schemas). The adapter translates, and any
  constraint the vendor cannot express is enforced after parsing.
- Handle refusals and `max_tokens` truncation as typed failures, never as empty
  success.
- One repair attempt, then `InvalidModelOutput`.
- Prompt name and version, schema version and model are stored with each call.
- External text (company descriptions, news, job text) is passed as delimited
  data and never as instructions (prompt-injection hygiene).
- Model name, temperature and limits come from integration settings, not code.
  Prices are not hard-coded.

## 13. Fixtures and recorded responses (#82, #83, #84, #87)

- CI runs fully offline. Contract tests use recorded or hand-written response
  fixtures served by an HTTP mock (for example `respx` or `responses`).
- Each adapter needs fixtures for at least: a normal success, a no-match, a
  partial record with missing fields, a 401, a 429 with `Retry-After`, a
  quota-exhausted response, a 5xx, and a timeout. Enrichment and verification
  add: unknown result, catch-all, and a quota error.
- Fixtures are **sanitized**: no real people, no real emails, no real keys.
  Replace names and addresses with obviously fake values (use reserved domains
  such as `example.com`). Company data for public companies is fine, but keep
  fixtures minimal.
- Record from the vendor's free tier or sandbox once, then sanitize by script
  and commit only the sanitized file. Keep the sanitizer next to the fixtures
  and say how to re-record.
- Fixtures record the vendor's response shape at a point in time. Keep a
  version note and the date recorded. A vendor-schema change shows up as a
  failing live smoke test, not a silent drift.
- An optional **live smoke test**, gated by environment variables and a marker
  such as `live`, runs by hand against the free tier or sandbox. It is never part
  of CI. It uses placeholders in docs and real keys only from the developer's
  own environment.
- Every adapter passes its category's shared contract test (#87), including the
  mock.
- A secret-redaction test fails if a key appears in logs, errors or
  `ProviderCall` rows.

## 14. Pull request checklist for a new adapter

- [ ] One category interface; metadata declares category, name, version,
      capabilities, config keys (secret or not), allow-listed hosts, rate
      limits, retention profile, `ai_processing_allowed` and units model.
- [ ] Licence reviewed; link to the terms and date; storage, caching, AI
      processing and export rights recorded; "unknown" is acceptable but stated.
- [ ] Source type of the vendor's data stated; no scraping dependency (Brief §4).
- [ ] Auth, timeouts, retries and rate limits use the shared layer.
- [ ] Every vendor error maps to a typed error; no vendor text leaks.
- [ ] Missing values are `None` with warnings; no defaults or guesses.
- [ ] Provenance fields complete; event date supplied only when the vendor has
      one.
- [ ] Cost and billable units logged; budgets honoured.
- [ ] Raw payload and cache follow the retention profile; vendor record ID kept.
- [ ] Personal data minimized and masked; no real data in fixtures.
- [ ] Offline contract tests and sanitized fixtures; optional gated smoke test.
- [ ] Docs: the integration catalog entry (#88) and, if it is a first-choice
      provider, an update to the ADR.

## Where this fits

- Shortlist and comparison: [provider-shortlist.md](provider-shortlist.md).
- Decision and open questions: [ADR 0012](../adr/0012-first-provider-selection.md).
- Adapter pattern: [ADR 0002](../adr/0002-modular-provider-adapters.md).
- LLM contract: [ADR 0003](../adr/0003-structured-llm-output.md).
- Keep history and AI versus human: [ADR 0007](../adr/0007-keep-history-and-separate-ai-from-human-decisions.md).
- The step-by-step guide for adding an adapter (#88) will link back here.
