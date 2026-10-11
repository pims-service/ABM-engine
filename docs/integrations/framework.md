# Integration framework

How every external service plugs into ABM Engine. The design is in
[ADR 0013](../adr/0013-integration-framework.md); the requirements an adapter
must meet are in [adapter-requirements.md](adapter-requirements.md). The code
is `backend/apps/integrations/framework/` (pure Python: no models, no
network, no vendor SDK). Issues #74 (base interface and registry) and #75
(result envelope).

## The pieces

| Piece | Where | What it is |
| --- | --- | --- |
| `IntegrationCategory` | `categories.py` | The nine Brief section 17 categories: `llm`, `company_data`, `people_data`, `email_enrichment`, `email_verification`, `crm`, `outreach`, `storage`, `automation`. |
| `Adapter` + category base classes | `base.py`, `operations.py` | `CompanyDataAdapter`, `PeopleDataAdapter`, `EmailEnrichmentAdapter`, `EmailVerificationAdapter`, `LLMAdapter`, `CRMAdapter`, `OutreachAdapter`, `StorageAdapter`, `AutomationAdapter`. |
| `AdapterRegistry` | `registry.py` | `(category, key)` to adapter class; `resolve(ConnectionSpec)` builds an instance. |
| `AdapterResult[T]`, `Provenance`, `Usage` | `envelope.py` | The envelope every operation returns. |
| Normalized types | `normalized.py` | `NormalizedCompany`, `NormalizedPerson`, `SignalCandidate`, `EmailVerification`, `EmailFinding`, `NormalizedJobPosting`, `CompanyRef`, `PersonRef`, `CompanyPage`. |
| Errors | `errors.py` | `ProviderError` family, `map_http_status`, `parse_retry_after`. |
| Fakes | `fakes.py` | One in-memory adapter per category, `register_fakes(registry)`. Tests and local development only. |
| Bridge | `bridge.py` | `as_company_search_provider(adapter)` for the M2 import contract (#66). |
| Contract tests | `backend/tests/adapter_contract.py` | The mixins every adapter's tests import. |

Importing the package registers nothing. `fakes` and `bridge` are imported
explicitly.

## Write an adapter, step by step

Example: a company data adapter for a vendor called Acme. Replace the names.

1. **Pick the category base class** from the table above. One adapter, one
   category. A vendor that does two things (company data and people) is two
   adapters that may share a private HTTP client.

2. **Create the module** under `backend/apps/integrations/adapters/acme/`
   (the vendor code lives only there). Application code must not import it:
   a test fails if anything outside `apps/integrations/` imports
   `apps.integrations.adapters`.

3. **Declare the adapter.**

   ```python
   from apps.integrations.framework import (
       AdapterCapabilities, AdapterResult, CompanyDataAdapter, CompanyRef,
       ConnectionTestResult, NormalizedCompany, Usage,
   )
   from apps.integrations.framework.base import RateLimit

   class AcmeCompanyAdapter(CompanyDataAdapter):
       key = "acme"                        # [a-z0-9][a-z0-9_.-]{0,63}, unique per category
       display_name = "Acme Data"
       version = "1.0.0"
       terms = "licensed_api"              # licensed_api | public_data | manual
       capabilities = AdapterCapabilities(
           features=frozenset({"enrich_company", "department_headcount"}),
           allowed_hosts=("api.acme.example",),
           rate_limit=RateLimit(per_minute=600),
       )
       config_schema = {
           "type": "object",
           "properties": {"region": {"type": "string", "enum": ["eu", "us"], "default": "eu"}},
           "required": [],
           "additionalProperties": False,
           "x-credentials": {"required": ["api_key"], "optional": []},
       }
   ```

   - `terms` is the legal basis. Scraping, "crawler" and unknown values are
     refused at registration. State the vendor's data source and link the
     reviewed licence in the pull request (adapter requirements, section 1).
   - `features` is the closed list for the category (`CATEGORY_FEATURES`).
     Declare only what you really implement; an undeclared operation is
     refused by `execute`. Fields you cannot fill stay `None`.
   - `config_schema` lists non-secret settings and the credential **names**.
     A setting called `api_key`, `token`, `password` and so on is refused:
     secrets are credentials.

4. **Implement the operations.** Take credentials with
   `self.credential("api_key").reveal()` only where you build the request
   header (never the query string). Build results with `self.provenance(...)`,
   which stamps your key, category, terms and clock.

   ```python
       def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
           payload = self._get("/v1/company", domain=company.domain)   # your HTTP layer (#78)
           if payload is None:
               return AdapterResult(None, self.provenance(source_ref="acme:lookup"))
           data = NormalizedCompany(name=payload["name"], domain=payload.get("domain"),
                                    employee_count=payload.get("employees"))
           return AdapterResult(
               data,
               self.provenance(source_ref=f"acme:{payload['id']}", request_id=payload.get("rid")),
               Usage(credits=1),
           )

       def test_connection(self) -> ConnectionTestResult:
           return self.timed_connection_test(lambda: self._get("/v1/ping"))
   ```

5. **Translate failures** to typed errors, never to vendor text. Use
   `map_http_status(response.status_code, retry_after=response.headers.get("Retry-After"))`
   and `raise ... from exc`. Network timeouts are `ProviderTimeout`; a body you
   cannot parse is `ProviderUpstreamError`; a quota message is
   `ProviderQuotaExceeded`. The mapping table is below.

6. **Never invent data.** A missing field is `None`. Evidence-bearing results
   (company, person, signal, verification) need provenance with a
   `source_url` (a real URL the vendor gave) or `source_ref` (the vendor's
   record or request id); the envelope raises `EnvelopeError` otherwise. A
   not-found lookup is `AdapterResult(None, provenance)` or `ProviderNotFound`,
   whichever the vendor means.

7. **Register it.** Registration is explicit, in the place M3 decides (app
   ready or settings). It is one line and has no import-time effect:

   ```python
   from apps.integrations.framework import register_adapter
   register_adapter(AcmeCompanyAdapter)
   ```

8. **Test it** with the contract mixin and a fake transport (no network; the
   mixin blocks sockets):

   ```python
   from tests.adapter_contract import CompanyDataContract

   class TestAcmeCompanyAdapter(CompanyDataContract):
       adapter_cls = AcmeCompanyAdapter

       def make_adapter(self):
           return AcmeCompanyAdapter({}, {"api_key": "YOUR_API_KEY"}, transport=FakeTransport())
   ```

   Add fixture-based tests for each vendor error (401, 429 with `Retry-After`,
   5xx, malformed body) and for missing fields staying `None`. Use placeholders
   such as `YOUR_API_KEY`; a secret-looking string needs
   `# pragma: allowlist secret`.

## Calling an adapter

```python
adapter = registry.resolve(ConnectionSpec(category, key, config, credentials))
result = execute(adapter, adapter.find_people, company_ref, ["head of sales"], limit=5)
```

`ConnectionSpec` is what the settings layer (#76) builds from a stored
integration config: category, key, non-secret config, credentials (`str` or
`SecretStr`). Unknown key: `ProviderNotFound`. Missing or invalid settings:
`ProviderNotConfigured` naming the fields (never values).
Capability negotiation: `supports(adapter, "funding")`,
`registry.supporting("company_data", "funding")`,
`registry.supports(category, key, feature)`.
`execute` refuses undeclared features, converts stray exceptions to
`ProviderUnavailable`, checks the result carries your key and category, and
fills `usage.latency_ms`.

## The result envelope

`AdapterResult(data, provenance, usage=Usage(), warnings=(), quality=None)`

- `provenance`: `provider`, `category`, `retrieved_at` (timezone aware),
  `terms`, `source_url` / `source_ref` (one required for evidence),
  `evidence_date` (the date the source states), `request_id`, `cache_hit`,
  `raw_ref` (a pointer to the raw payload kept elsewhere, scrubbed, at most
  200 characters).
- `usage`: `credits`, `cost` (Decimal) with `currency`, `input_tokens` and
  `output_tokens` and `model` for LLMs, `latency_ms`.
- `warnings`: at most 20 short, scrubbed messages for a partial answer.
- `quality`: optional `high`, `medium` or `low`.
- `log_summary()` is the secret-free dict for logs and call records.

Normalized types bound every text field, scrub secrets embedded in free text,
turn invalid values into `None` (a country that is not ISO alpha-2, a negative
headcount, a percent outside -100 to 100000) and keep percent changes as
`Decimal`. Required parts raise `EnvelopeError`: a name, a signal's type,
evidence and source. Signal types equal `apps.research.models.SignalType`
(a test keeps them in sync).

## Errors and retry behavior

| Error | Code | Retry | HTTP statuses |
| --- | --- | --- | --- |
| `ProviderAuthError` | `provider_auth` | never | 401, 403 |
| `ProviderRateLimited` | `provider_rate_limited` | after `retry_after` | 429 |
| `ProviderUnavailable` | `provider_unavailable` | backoff | other 5xx |
| `ProviderTimeout` | `provider_timeout` | backoff | 408, 504 |
| `ProviderBadRequest` | `provider_bad_request` | never | 400, 405, 413, 414, 415, 422 |
| `ProviderQuotaExceeded` | `provider_quota_exceeded` | not until credits reset | 402 |
| `ProviderNotConfigured` | `provider_not_configured` | never | none |
| `ProviderNotFound` | `provider_not_found` | never | 404, 410 |
| `ProviderConflict` | `provider_conflict` | never | 409 |
| `ProviderUpstreamError` | `provider_upstream_error` | never | anything else |
| `ProviderInvalidOutput` | `provider_invalid_output` | not here (LLM repair is #81) | none |

Messages are fixed per class. `as_dict()` returns `code`, `message`,
`provider`, `retryable`, `retry_after` (plus `field` or `fields`) and is safe
for an API response or a log line. The shared retry layer (#78) and the job
base (#86) read `retryable` and `retry_after`; they never parse messages.

## What this PR does not do

No models, migrations or endpoints. Credentials storage and the
`IntegrationConnection` model are #76; the settings API and Test Connection
button are #77; the HTTP client, retries and limiter are #78; the first vendor
adapters are #81 to #85.
