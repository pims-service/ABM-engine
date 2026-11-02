# Evaluating the qualification step

A labelled set of cases and a command that scores them, so a change to the prompt, the model, the
campaign rules or the hard rules can be judged before it reaches a client (Brief sections 2, 6 and
7). The cases are in `backend/apps/research/evals/cases/`, the harness in
`apps/research/evals/harness.py`.

## What a case is

One JSON file: the campaign's rules, a company's facts, its dated signals, the day it is judged
(`as_of`), the model's answer (`model_output`) and the expected outcome:

```json
"expected": {"fit": "strong", "recommendation": "add", "trigger": false}
```

The outcome is judged in the same order as the real analysis: the model's verdict (checked against
the schema), then the campaign's **hard rules** (exclusions, business model, size: a model cannot
override them), with the trigger decided **separately** from the signals by their freshness windows
(including a campaign's own windows, see [trigger windows](trigger-windows.md)). Fit and trigger
never influence each other, so a strong fit with no trigger must stay an ADD.

The set holds 14 invented cases: strong fit with no trigger (including an Arabic company), strong
fit with a fresh trigger, a stale hiring signal, funding inside the default window and beyond a
campaign's shorter one, an excluded industry and an excluded company type, a consumer company for
a B2B campaign, a company above and below the size range, an unknown size, a weak fit the model
called an ADD, and a company whose own text tells the model to rate it strong (an injection
attempt that the exclusion still defeats).

## Running it

```bash
python manage.py run_evals                         # recorded answers, offline, no database
python manage.py run_evals --out run.json          # also write the report
python manage.py run_evals --against run.json      # and print what changed since that report
python manage.py run_evals --case strong-no-trigger-saudi-fintech
```

It prints how many cases pass, the agreement per field (fit, recommendation, trigger) and per
expected fit class, and lists every disagreement with the field and the two values. With
`--against` it names the cases that **regressed** or were **fixed**, whose outcome changed, and the
cases added or removed. It exits with an error when the pass rate is below `--fail-under` (1.0 for
recorded answers) or when a case that passed before fails now.

In CI the recorded run is a test (`tests/test_evals.py::test_every_recorded_case_passes`): a change
to the hard rules, the schema or the trigger windows that breaks a labelled outcome fails the build.
When a rule changes on purpose, change the label in the case file in the same commit.

## Recorded and live answers

The committed `model_output` of each case is **scripted**: written by hand as a model that follows
the instructions (`"model_output_source": "scripted"`), because this repository has never run
against a real model. So the recorded run tests everything around the model, not the model itself.

To measure a real model, run it live against a client's AI connection (Settings > Integrations,
category LLM). The connection holds the key; nothing goes in the repository and CI never runs it:

```bash
python manage.py run_evals --live --client <client id>
python manage.py run_evals --live --client <client id> --record
```

`--record` stores the live answers in the case files (`recorded:<model>@<date>`), so later recorded
runs replay what that model actually said. A failing model call fails that case, not the run. Live
runs use the connection's budget like any other call.

## Changing a prompt

The prompt is versioned (`ASSESS_VERSION` in `engine/prompts.py`) and the version is stored with
every assessment and recommendation, with the model name and the schema version. To keep a changed
prompt from keeping its old version, `prompt_fingerprint.json` holds a hash of the prompt as it is
rendered for a fixed sample campaign, and a test fails when the prompt changes without it. After
changing the wording:

1. bump `ASSESS_VERSION`;
2. run `python manage.py run_evals --write-fingerprint`;
3. run `run_evals --live --client <id> --out new.json --against old.json` and read the regressions;
4. commit the new fingerprint (and re-recorded answers, if you recorded).

## Adding a case

Copy a file in `cases/`, give it a new `id`, change the campaign, company, signals and
`model_output`, and set `expected` to the outcome **a person would want**, not to whatever the
system currently does. Use invented companies only. Tag it (`strong_icp_no_trigger`, `exclusion`,
`negative`, ...): a test checks that the tags the brief insists on stay covered.

## Not covered

* The extraction of facts from a website (the first model call) and the outreach messages are not
  evaluated; this set covers the ICP fit and recommendation and the trigger.
* With scripted answers the set cannot say how good a model is. Record real answers first.
* The set is small and invented. It guards the rules and the contract; it does not estimate
  accuracy on real accounts.
