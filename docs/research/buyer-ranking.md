# Buyer ranking: rules, stored runs and the AI explanation

Who to contact first at a company (Brief sections 8 and 15). The ranking is rules first, so it can
be read and corrected; a model only explains it and breaks ties when asked.

## The rules (`apps/research/buyers.py`)

Pure rules over the people already entered for the company, the campaign's preferred buyer titles
and target departments, and the company size:

* The campaign's ordered **preferred titles** decide first. An exact title beats a title that
  contains the preferred one. Short forms are compared after normalising (VP, BD, CEO, CCO,
  "Vice President Business Development" matches "VP BD").
* **Small company** (49 employees or fewer): the founder or CEO usually decides, so founders, the
  CEO, the managing director and the owner rank first (the brief's small company example).
* **Larger company**: the CEO is not assumed to be best. Without preferred titles the brief's order
  is used (BD leaders, commercial leaders, then the managing director and the CEO).
* **Unknown size**: the campaign's title order is used as it is, and the answer says so.
* A person whose title matches nothing ranks last and is never suggested as primary.

Every position has its reasons in words. The result is a suggestion: applying it (the primary and
secondary roles on the contacts) is a separate step, **Apply** (`apply-buyer-ranking`).

## Stored runs

`POST /companies/{id}/buyer-ranking/run/` stores the ranking as a **run** (`BuyerRankingRun`,
append-only). A run records the order, the reasons and a hash of what it was made from: the
campaign's preferred titles and target departments, the company size and the people.

* **Re-run** makes a new run and keeps the old ones.
* **Unchanged inputs** make nothing: the latest run is returned (`created: false`) unless
  `force` is true.
* **Stale**: `GET /companies/{id}/buyer-ranking/runs/` compares each newest run's hash with the
  current inputs. The newest run is `current` or `stale` (the buyer rules, the company size or the
  people changed since); older runs are `superseded`. Staleness is computed when read, so a rule
  change marks every existing ranking stale without altering a row.
* **Many accounts**: `POST /campaigns/{id}/accounts/rerun-buyer-ranking/` takes up to 200 company
  ids and answers `created`, `unchanged` or `error` per account.

## The AI explanation and tie-break (`apps/research/buyer_explain.py`)

With `explain: true` the run adds a model step with two narrow jobs:

1. **Explain** why the first person is the best target, from the rule reasons.
2. **Break a tie**: when two or more people score the same at the top, choose between exactly those
   people. The chosen person moves to rank 1 and gets a reason saying the model chose them.

The model never adds a person. It returns ids, which are checked against the candidates it was
given (`BuyerChoice`, extra fields forbidden): a primary outside the allowed set, or a secondary
that is not another candidate, is refused and the model gets one more try. After that, and when
there is no AI connection or the provider fails, the run keeps the rules ranking with a plain
explanation built from the rule reasons. The run's `explanation.source` says `model` or `rules`,
and `explanation.note` says why the model was not used. The prompt is versioned (`buyer_choice` v1)
and stored with the run (`model_name`, `prompt_version`, `schema_version`). Tokens and cost are logged
with each call as a provider call, like every model use, and count against the connection's budget.

Candidate names and titles come from outside data, so they go into the prompt as untrusted text.

## Tuning

* Change who ranks first with the campaign's **preferred buyer titles** (ordered, most preferred
  first) and **target departments**. Existing runs turn stale; run again.
* The short forms are in `_PHRASES` and the small company limit in `SMALL_COMPANY_MAX`
  (`apps/research/buyers.py`); the default large-company order is `DEFAULT_LARGE_TITLES`.
* The model wording is `buyer_prompt` in `buyer_explain.py`. Change `PROMPT_VERSION` with any
  wording change.

## When it looks wrong

* **Nobody is primary.** Nobody's title matches a preferred title or a target department, so no
  one scores above zero. Add the titles that decide at this kind of company, or add the person.
* **The run says `unchanged` but you changed something.** Only the preferred titles, target
  departments, company size and people count. A change to the offer or other rules does not.
  Use `force` if you want a new run anyway.
* **The explanation is from `rules`.** Read `explanation.note`: usually no AI connection under
  Settings > Integrations, a provider error, or a model answer that named someone who is not a
  candidate twice.

## Not built

A screen for runs: the card still shows the live suggestion (`buyer-ranking/`). Stored runs are
used through the API. The explanation is not re-checked by a person before it is shown.
