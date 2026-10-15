# LLM adapter and structured output

Issue #81. Brief section 18: where AI output controls application logic, use structured
schemas, not prose. [ADR 0003](../adr/0003-structured-llm-output.md) sets the rules; this page
is the how-to. Prompts for ICP and trigger logic belong to M5; this is the plumbing.

Code: `backend/apps/integrations/llm/` (provider-agnostic) and
`backend/apps/integrations/adapters/anthropic/` (the first concrete adapter). Nothing is
registered on import, and there are no models, migrations or API endpoints.

## Define a schema, call it

```python
from pydantic import BaseModel, Field

from apps.integrations.framework import PromptSpec
from apps.integrations.llm import pydantic_schema, structured_call, wrap_untrusted, UNTRUSTED_SYSTEM_NOTE


class FitVerdict(BaseModel):
    label: str = Field(pattern="^(fit|no_fit|unknown)$")
    evidence_quote: str | None  # a verbatim quote, or None when the evidence says nothing
    reason: str


prompt = PromptSpec(
    name="icp_fit",
    version="1",  # bump on every wording change
    system="Classify fit. Quote only from the data. " + UNTRUSTED_SYSTEM_NOTE,
    user="Company page:\n" + wrap_untrusted(page_text, "company_page"),
)
result = structured_call(adapter, prompt, pydantic_schema(FitVerdict), max_tokens=500)
verdict: FitVerdict = result.data
```

`adapter` is any `LLMAdapter` (from the registry, or a `FakeLLMAdapter` in tests); swapping the
provider is a settings change. `temperature` defaults to 0 (use it for classification and
extraction). `max_tokens` caps the answer.

## What `structured_call` does

1. Calls `adapter.generate_structured` through `execute` (typed errors, declared-feature check).
2. If the output does not validate: ONE repair attempt, with the original prompt plus the field
   paths and validator codes that failed. The rejected text is never fed back. `max_repairs` is
   0 or 1; anything else raises `ValueError`. There is no loop.
3. A second failure raises `StructuredOutputError` (a `ProviderInvalidOutput`, code
   `provider_invalid_output`) with `issues` (paths and codes), `usage` and `attempts`.
4. Usage (tokens, cost, latency) is summed over all attempts, failed ones included.
5. The prompt name and version are in `result.provenance.source_ref` (`prompt:name@version`) and
   in the `llm_call` log line with tokens and model. Prompt text and output are never logged.
6. Other errors (auth, rate limit, timeout) are not repaired; retry policy is the job layer's.

## Error handling

| Error | Meaning | Retry |
| --- | --- | --- |
| `StructuredOutputError` | Output invalid after the repair (also refusal or `max_tokens` stops) | No: change the prompt or schema |
| `ProviderAuthError`, `ProviderQuotaExceeded` | Key or billing | No |
| `ProviderRateLimited` (`retry_after`), `ProviderUnavailable`, `ProviderTimeout` | Transient | By the job layer |
| `ProviderBadRequest` | Our request is invalid (for example `max_tokens`) | No |
| `UngroundedOutput` | `assert_grounded` found values not in the evidence | No |

Messages are fixed sentences; they never contain model output, upstream text or credentials.

## Grounding and prompt injection

- `wrap_untrusted(text, label)` puts external text in `<untrusted_data label="...">`, defuses a
  forged closing tag, strips control characters and caps the length. Pair it with
  `UNTRUSTED_SYSTEM_NOTE` in the system prompt. It lowers the risk; it does not remove it. Keep
  output schema-bound and never let it trigger actions by itself.
- `assert_grounded({"quote": q}, evidence)` raises `UngroundedOutput` (field names only) unless
  every value occurs in the evidence after normalisation (case, whitespace, curly quotes,
  dashes). Limits: it is a substring test. It shows a quote exists in the evidence, not that the
  claim is true or relevant; paraphrases and reformatted numbers fail, so ask for verbatim
  quotes; values shorter than 3 characters are never accepted. Use it before showing a trigger
  or personalisation point (Brief section 12).

## Anthropic adapter

Key `anthropic`, terms `licensed_api`, feature `structured_output`, credential `api_key`.

| Setting | Default | Notes |
| --- | --- | --- |
| `model` | none (required) | Taken as configured; no model name is built in |
| `api_base` | `https://api.anthropic.com` | https, host allow-listed (`api.anthropic.com`) |
| `anthropic_version` | `2023-06-01` | `anthropic-version` header |
| `timeout_seconds` | 60 | 1 to 300 |
| `send_temperature` | false | See "not verified" |

`test_connection()` calls `GET /v1/models?limit=1`: it authenticates the key and generates no
tokens (`test_connection_costs_credits` is false). It does not check the model exists.

The transport is injectable (`transport=`); tests use a fake and never the network. The real one
(`UrllibTransport`, standard library only) allows https and the allow-listed host only, verifies
TLS, sets a timeout, never follows redirects, caps the response at 4 MiB, and never logs URLs,
headers or bodies. HTTP statuses go through `map_http_status`; `retry-after` is parsed.

### Verified against official documentation (retrieved 2026-10-11)

- Messages API, endpoint, headers, response `content`, `usage`, `model`:
  <https://platform.claude.com/docs/en/api/messages>
- Structured outputs (`output_config.format` with `type: json_schema`, GA without a beta header,
  JSON in a text block, `additionalProperties: false` required, unsupported keywords,
  `refusal` and `max_tokens` caveats, supported model ids):
  <https://platform.claude.com/docs/en/build-with-claude/structured-outputs>
- Errors (400, 401, 402, 403, 404, 409, 413, 429, 500, 504, 529), `request-id`, and that
  forced tool use (`tool_choice` any or tool) is rejected by the current models, which is why
  tool-use JSON is not used: <https://platform.claude.com/docs/en/api/errors>
- Models list: <https://platform.claude.com/docs/en/api/models-list>
- `anthropic-version: 2023-06-01`: <https://platform.claude.com/docs/en/api/versioning>

### Not verified

- The accepted range of `temperature`, and whether the newest models accept it: the Messages
  reference lists no such parameter. It is therefore sent only with `send_temperature: true`.
  The adapter itself limits it to 0 to 1.
- That the models list call is free of charge (assumed: it generates no tokens).
- Which model to use. The repo's current names are `claude-sonnet-5-5`, `claude-opus-5-5`,
  `claude-fable-5-1` and `claude-haiku-4-5-20251001`; the provider choice is still a proposal
  ([provider shortlist](provider-shortlist.md)).
- No call was made to the live API (no key, no network in tests).


## Metering, budgets and the key test

`apps.integrations.llm_connection.get_llm_adapter(client_id, job=, campaign=, user=)` is how the
product obtains the client's model adapter. The adapter it returns is metered: every call writes
one `ProviderCall` row (tokens, who, campaign, failures too) and the connection's monthly budget
is checked first, so a call over the limit raises `BudgetExceeded` and nothing is sent. The
Anthropic adapter reports usage in thousands of tokens as `credits` (no price list is built in),
so a **credit limit** on the connection caps monthly usage; set it on the connection's budget.
Settings > Integrations "Test connection" asks the provider to authenticate the key (no tokens are
generated); it does not check that the configured model exists.
