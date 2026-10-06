# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-10-06

## Context

ABM Engine is built by a small team over several milestones. Some choices
(which vendors we depend on, how AI output is handled, how auth works) shape
everything that comes after them. If the reasoning only lives in chat threads
and people's heads, newcomers cannot tell a deliberate choice from an accident,
and we end up re-arguing settled questions.

## Decision

We will record significant architecture decisions as short ADRs in
`docs/adr/`, using the MADR-style [template](template.md).

- One decision per file, named `NNNN-short-title.md`, numbered in order.
- Each ADR states the context, the decision, the alternatives we weighed and
  the consequences. Aim for about one page.
- ADRs are reviewed in a pull request like code.
- An accepted ADR is not rewritten when things change. We add a new ADR and
  mark the old one "Superseded by NNNN". Small typo fixes are fine.
- The index lives in [README.md](README.md).

## Alternatives considered

- **Wiki or Notion pages**: easy to write, but they drift away from the code,
  have no review step and no history tied to commits.
- **Long design documents**: useful for big features, but too heavy for
  individual decisions and rarely kept up to date.
- **Nothing formal, rely on PR descriptions**: cheap, but the reasoning is hard
  to find later and is scattered.

## Consequences

- Decisions and their trade-offs are easy to find and reviewable.
- A small writing overhead for each significant decision.
- We need some judgement about what is "significant". Rule of thumb: if
  reversing it later would touch many files or milestones, write an ADR.
