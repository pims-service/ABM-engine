# AI versus human agreement

Because the AI recommendation and the human decision are stored apart, we can see how often people
agree. `/companies/agreement` (button "AI vs you" on the Companies page) shows, for the selected
campaign and an optional date range:

* the agreement rate: each company's latest decision in the range against the recommendation it
  answered. Decisions made with no recommendation are counted separately and left out of the rate;
* a 3x3 table of AI add/hold/skip against your add/hold/skip;
* how many times each flag (wrong buyer, not B2B, ...) was raised.

`GET /api/v1/campaigns/{id}/accounts/agreement/?start=&end=` returns the same numbers.

## Using the feedback export

"Export feedback" (`GET .../accounts/feedback-export/`) gives one row per flag with the note, the
ICP fit with its reasons and concerns, the AI recommendation and explanation, and the prompt version
and model of the latest AI run. To improve the prompts and rules:

1. Filter the CSV by flag and look for the prompt version that keeps appearing.
2. Read the reasons next to the notes: a repeated "wrong buyer" with the same reasoning points at the
   buyer rules; "not B2B" at the fit prompt.
3. Change the prompt or rule, bump its version, re-run a few accounts and compare the next export.

Needs the manager level. Cells are protected against spreadsheet formulas. This is intentionally
small: no charts, no scheduled reports.
