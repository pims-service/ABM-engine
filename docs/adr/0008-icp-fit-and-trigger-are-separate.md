# 0008. ICP Fit and Trigger are separate concepts

- Status: Accepted
- Date: 2026-10-06

## Context

Brief §2 calls the split between ICP Fit and Trigger critical to the product.

- **ICP Fit** asks: is this company fundamentally a good prospect for our
  offer? Values: Strong, Medium, Weak.
- **Trigger** asks: is there a reason contacting them right now is especially
  relevant? Values: Yes or No.

A company does not need a trigger to qualify. Strong ICP with No Trigger is
still eligible for outreach (Brief §2, §6: do not reject a suitable account
merely because there is no current trigger). Brief §7 says triggers are
evaluated separately from ICP, can be multiple, and need freshness or expiry
logic so stale events do not stay "current" forever. Brief §15 says the core
output must not be an unexplained score such as "82/100".

## Decision

We will model ICP Fit and Trigger as two separate assessments with separate
reasoning.

- **ICP assessment**: a Strong, Medium or Weak value, with reasons and
  potential concerns, judged against the campaign's rules.
- **Trigger assessment**: Yes or No. Each detected trigger is its own record
  with a type (for example sales hiring, funding, new leadership), evidence,
  the date of the event, the source, and a freshness or expiry rule. A company
  can have several. The overall answer is Yes only while at least one trigger
  is still fresh, so stale events stop counting.
- Both are stored as separate records with timestamps, following ADR 0007, and
  produced as separate fields of structured output, following ADR 0003.
- Eligibility depends on ICP Fit and the campaign's rules, never on the
  presence of a trigger. A trigger can raise priority or shape the outreach
  angle, but it is not a gate.
- We will not merge the two into one score. Each is shown with its own
  explanation.
- Trigger evidence must cite a source (ADR 0003). No source, no trigger.

How long each trigger type stays fresh, and how a trigger affects ranking, are
open details to settle during implementation.

## Alternatives considered

- **A single combined score**: easy to sort by, but it hides why an account
  ranks where it does, breaks Brief §15, and lets a missing trigger drag down
  a strong account, which the Brief forbids.
- **Trigger as an input to ICP Fit**: it seems tidy, but fit is about the
  company in general and timing is about now. Mixing them makes fit change as
  events come and go, and makes a Strong account look weaker when quiet.
- **Trigger as a required qualifier**: simple to explain, but it throws away
  good prospects with no current event, directly against Brief §2.
- **A boolean trigger flag with no detail**: cheap, but with no type,
  evidence or date we cannot explain it, expire it or let users correct it.

## Consequences

- Users see two clear answers and can sort or filter on each, for example
  "Strong ICP, no trigger" as a valid outreach list.
- Each can be corrected on its own, including the feedback "signal should not
  count as a trigger" from Brief §16.
- More fields and records than a single score, and the UI must present both
  without overwhelming people.
- Trigger freshness needs a rule and something that re-evaluates it over time,
  for example a scheduled job (ADR 0005).
- Prompts and schemas must keep the two judgements apart, so the model does not
  let a trigger sway the fit rating.
- Revisit if users consistently need a combined ranking. Even then, we would
  derive it from the two assessments, not replace them.
