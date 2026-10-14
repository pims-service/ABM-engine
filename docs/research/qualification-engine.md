# Qualification engine

Brief sections 5 to 7 and 9. "Analyze Company" runs `apps.research.engine.run_analysis` in the
worker (`apps/companies/tasks.py`). It turns a company website into evidence-backed research, a
separate ICP assessment and an AI recommendation. A person always makes the final call.

## What a run does

1. **Reads the website** with the safe fetcher ([website fetching](../integrations/website-fetching.md)):
   the homepage plus about, products, careers, news and contact pages where they exist.
2. **Extracts facts and dated signals** (LLM call 1, `company_facts`). Facts the pages do not
   state stay `null`. A signal is kept only if it has an event date that is not in the future, a
   source page that was actually fetched, and an `evidence_quote` that appears verbatim on the
   pages. Anything else is dropped. An employee count is kept only if the number is on a page.
   Saved as a `CompanyResearch` snapshot, `DataSource` rows (URL and time of each page) and
   `Signal` rows with an expiry (`engine/freshness.py`: 60 days for hiring, 120 for growth, 180
   for expansion type events, 365 for funding).
3. **Judges ICP fit** (LLM call 2, `icp_fit`) against the campaign rules and the saved facts.
   The prompt says a trigger is not required. The result is stored as an `ICPAssessment` (fit,
   reasons, concerns) and an `AIRecommendation` (add, hold, skip, explanation). The outreach
   angle is stored in the recommendation's `raw_output`.
4. **Hard rules** (`engine/rules.py`) run after the model and only make the outcome more
   cautious: an excluded industry or company type forces weak and skip; a B2C company in a B2B
   campaign (or the reverse) is weak and never an add; a company size outside the range caps the
   fit at medium; a weak fit is never an add. Each change adds a concern that says why.

Every row is append-only, so a re-run adds a new snapshot and the history stays. The same event
is not recorded twice.

## What it needs

An active **LLM connection** for the client (Settings > Integrations, category LLM, provider
Anthropic, with a `model` in the config and the `api_key` credential). Without it the job fails
with a message that says so and the company stays `pending`. Nothing is made up.

## What it does not do yet

Headcount (sales, BD, marketing) and its 3, 6 and 12 month growth need a licensed company-data
provider, and buyer ranking needs a people-data provider. Those fields stay empty and the
research card says so.
