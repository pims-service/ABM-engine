# Research engine: collectors, evidence and history

How a company gets its facts and signals from outside sources, where every fact is recorded, and
how to add a new source. This is the data-collection half of milestone M4; the website-plus-AI
half is the [qualification engine](qualification-engine.md), and the growth arithmetic is in
[headcount growth](headcount-growth.md).

## What a run does

`POST /companies/{id}/research/` queues one background job (`company_research`) with one step per
item. Steps run in this order, and a step that fails does not stop the next one.

```
POST /companies/{id}/research/
        |
        v
  Job company_research  ---- queued, picked up by the Django-Q2 worker
        |
        +-- item profile ---> company-data adapter ---> evidence (DataSource) + snapshot
        |                       enrich_company            CompanyResearch (append-only)
        |
        +-- item signals --->  provider signals, last funding round, job postings
        |                       -> department by job title (taxonomy) -> Signal rows
        |
        +-- item news ------>  news source (skipped when none is configured)
                                -> keyword tag -> Signal rows
```

The job ends `succeeded`, `partial` (some step failed) or `failed` (all did). Each item keeps
what its step found (counts, `missing_fields`, a `skipped_reason`), which `GET
/companies/{id}/research-runs/` returns. A second request while a run is queued or running returns
that run (`already_queued: true`). A run does not read the website or call the AI model; that is
**Analyze Company**, which is unchanged.

## What gets stored

No new tables. A run is a `Job` with `JobItem` rows; everything else uses the existing models.

| Thing | Stored as | Written by |
| --- | --- | --- |
| Where a fact came from | `DataSource` (type, name, URL, `retrieved_at`, `evidence_date`) | `research.evidence.record_evidence` |
| What the provider said about the company | `CompanyResearch` snapshot, append-only | `companies.services.add_research_snapshot` |
| A hiring, funding or news trigger | `Signal`, append-only, with a source and an event date | `research.services.create_signal` / `supersede_signal` |
| One run | `Job` + one `JobItem` per step | `research.orchestrator.request_research` |

Rules that hold for every collector:

* **No source, no fact.** `record_evidence` refuses an empty source name, a retrieval time or an
  event date in the future, and a news or website source without a URL. The database also refuses
  a snapshot or a signal without a source.
* **Unknown stays unknown.** A field the provider did not return is `null` and is listed in
  `missing_fields`. Nothing is guessed, and a job title that matches no department is never put
  into Sales.
* **No invented dates.** A `Signal` needs the date of the event. A candidate without one is
  counted in the run report (`undated`) and not stored; a date in the future is refused.
* **Each event once.** If a second source reports an event we already hold (same type, same day),
  the existing signal is superseded by a new row that carries the new source, so the event
  appears once and both sources stay on record. The same source reporting it again changes
  nothing and creates no new source row.
* **No LinkedIn scraping.** Nothing in this engine fetches a LinkedIn page. A LinkedIn company
  URL is stored as an identifier only. People and company data come from licensed providers
  through the adapter framework.

## The profile step

`collect_profile` asks the company-data adapter to enrich the company and writes one snapshot:
industry, description, headquarters, employee count, the sales, business development, marketing
and commercial-plus-partnerships headcounts, and the 3, 6 and 12 month change.

* The headcounts the provider states become observations for the growth module; the percentages
  it gives directly are used only for the windows one observation cannot answer. The result goes
  to `headcount_change_*` and the `department_growth` JSON (version 1).
* Running it again with the same provider data adds no snapshot and no source. Changed data adds
  a new snapshot; the old one stays in the history.
* `GET /companies/{id}/profile/` returns one value per field across all snapshots. The order of
  kinds is `RESEARCH_SOURCE_PRECEDENCE` (default manual, provider, website, news); inside a kind
  the newest snapshot wins. Values that lost are listed as alternatives with their source.
  Snapshots themselves are not altered.

## The signal steps

Job titles are classified by `apps/research/taxonomy.py` using the rules in
`apps/research/data/department_rules.json` (English and Arabic; point
`RESEARCH_DEPARTMENT_RULES_FILE` at your own copy to change them without a deploy). A title that
names one department is counted; one that names several, or only an ambiguous phrase such as "Head
of Growth", is low confidence and not counted into a department.

| Signal | From |
| --- | --- |
| `sales_hiring`, `bd_hiring`, `commercial_hiring`, `partnerships_hiring` | the provider's job postings, one signal per department: "3 open sales roles: ..." dated by the newest posting |
| `funding` | the provider's last funding round, when it has a date |
| any type the provider reports | the provider's own signal candidates |
| `funding`, `new_leadership`, `new_office`, `market_expansion`, `new_product`, `major_partnership` | news items, tagged by keyword |

Each signal gets `expires_at` from `engine/freshness.py`.

## News

A news source is an object with `fetch(company, since)` returning `NewsItem` rows. **No real news
provider is built in.** `RESEARCH_NEWS_PROVIDER` is empty by default, which skips the step and
says so in the run. The value `fake` (offline, development and tests only) exists to exercise the
flow. News items are tagged by keyword rules (`tag_item`); an item that matches no rule, has no
link or no date, or is older than `RESEARCH_NEWS_WINDOW_DAYS` is counted and left out.

## Evidence and history endpoints

| Endpoint | Answers |
| --- | --- |
| `GET /companies/{id}/evidence/` | each source once, newest first, with age in days and what it backs |
| `GET /companies/{id}/research-history/` | snapshots newest first with what changed since the one before |
| `GET /companies/{id}/research-history/diff/?from=&to=` | what changed between any two snapshots |
| `GET /companies/{id}/research-runs/` | the latest 20 runs and their steps |
| `GET /companies/{id}/profile/` | the merged profile and where each value came from |

Snapshots are append-only and are never deleted, so `RESEARCH_HISTORY_DAYS` is a view window, not
a purge: it hides snapshots older than that many days from the history list (0 shows all), always
shows the newest, and still compares the oldest listed one with the snapshot before it.

## Settings

Plain Django settings in `config/settings/base.py`; none is an environment variable or a secret.

| Setting | Default | Meaning |
| --- | --- | --- |
| `RESEARCH_DEPARTMENT_RULES_FILE` | `""` (shipped rules) | path to a department rules JSON file |
| `RESEARCH_SOURCE_PRECEDENCE` | `manual, provider, website, news` | which kind of source wins in the merged profile |
| `RESEARCH_NEWS_PROVIDER` | `""` (no news) | `""` or `fake` |
| `RESEARCH_NEWS_WINDOW_DAYS` | `365` | how far back news counts |
| `RESEARCH_DEV_COMPANY_ADAPTER` | `""` (none) | dotted path of the offline company-data adapter; set in the dev and test settings only |
| `RESEARCH_HISTORY_DAYS` | `0` (all) | how much history the API lists |

A real provider connection is configured under Settings > Integrations as for every other
integration. Until a company-data adapter exists for a provider, a client with such a connection
gets "not supported yet", and the offline fake is used only where `RESEARCH_DEV_COMPANY_ADAPTER` names it (dev and test settings).

## Adding a collector

1. Get the data through an adapter (`apps/integrations`) or a news source; never fetch pages
   yourself.
2. Record where it came from with `record_evidence(...)`. Do not construct `DataSource` directly:
   a test fails if any code outside `companies.services` does.
3. Write facts with `add_research_snapshot` and triggers with `store_signal`, which dedupes and
   keeps both sources when two agree.
4. Add a function `(company, job, state) -> dict` to `STEPS` and `_STEP` in
   `apps/research/collect/run.py` and put its counts in the returned dict; the job item stores it.
5. Test it with the fake adapter, and test the unconfigured case: say what to configure, invent
   nothing.

## Known limits

* No real company-data or news provider is connected yet; the collectors are tested through the
  offline fakes.
* Provider snapshots can become the "latest snapshot" the company card shows; the merged profile
  endpoint is where source precedence is applied.
* News tagging is keyword-based, not language-model-based.
