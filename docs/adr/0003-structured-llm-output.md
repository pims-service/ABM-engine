# 0003. Structured LLM output via schemas

- Status: Accepted
- Date: 2026-10-06

## Context

Brief §18 says that where AI output controls application logic, we use
structured schemas rather than free-form prose. The AI decides things like ICP
fit, whether a trigger exists, the recommended status (add or skip), the
primary buyer role and the outreach angle. Application code branches on those
values, so a malformed or invented answer is a bug, not just a bad sentence.

LLMs sometimes return broken JSON, values outside the allowed set, or claims
with no evidence behind them.

## Decision

We will treat every LLM call that drives logic as a typed contract.

- Each such call has an output schema defined as a Pydantic model, with
  enumerated values where the set is fixed (for example `icp_fit`).
- Output is parsed and validated before anything else uses it. Free-form text
  is never parsed with string matching to make decisions.
- On invalid output we retry a small, bounded number of times, feeding back
  the validation error. If it still fails, the step is marked failed and
  surfaced for a human, not guessed at or silently defaulted.
- Prompts and schemas are versioned. Each stored AI result records the prompt
  version, schema version and model used, so results can be compared and
  reproduced.
- Claims must cite a source. Research claims carry a reference to the data
  source or evidence they came from, and a claim without one is rejected by
  validation or shown as unsupported. The model must not fabricate evidence.
- Prose fields (such as reasoning or a drafted message) are allowed inside the
  schema, but the logic-driving fields stay separate and typed.

## Alternatives considered

- **Free-form prose, then regex or keyword parsing**: easy to start, fragile
  in practice, and failures are silent.
- **Provider-native JSON or tool-calling modes only**: helpful and we can use
  them through the LLM adapter (ADR 0002), but they do not guarantee our
  business rules, and features differ by vendor. Our own validation stays the
  source of truth.
- **Validate only at the edges, trust internally**: cheaper, but bad values
  would spread before they are caught.
- **Unbounded retries**: raises cost and can loop on a prompt that does not
  work. Bounded retries plus a visible failure is safer.

## Consequences

- Downstream code can rely on typed, validated values.
- Failures are visible and counted, which helps us improve prompts.
- Versioning lets us change a prompt without losing track of older results.
- More work per prompt: writing a schema, tests with sample outputs, and
  bumping versions on change.
- Retries cost extra tokens and latency.
- Citations make outputs longer and mean "no evidence found" becomes a
  legitimate result instead of a filled-in guess.
