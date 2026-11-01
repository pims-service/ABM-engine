# Adding companies: manual, CSV and provider import

Companies are the front door of the engine: nothing is researched, qualified or contacted until
a company is in a campaign. There are three ways to add them, and all three go through the same
checks, so a company is never added twice and existing data is never overwritten.

| Path | Where | Best for |
| --- | --- | --- |
| [Manual](#1-manual-entry) | Companies, **Add one** | A single company you just heard about |
| [CSV file](#2-csv-upload) | Companies, **Import a CSV** | A list you already have, up to 5,000 rows |
| [Provider](#3-find-companies-at-a-provider) | Companies, **Find from a provider** | Seeding a campaign from its own ICP filters |

Every import belongs to the **campaign selected in the header**. Pick a client and a campaign
first. Importing needs the *manager* or *admin* level on the client (see
[permissions](permissions.md)); someone from another client cannot see or touch your batches (they
get a 404).

**What we do not do.** We do not scrape LinkedIn or any other site to find companies. A
LinkedIn (or other) profile URL is stored only as an **identifier**, to recognize the same
company twice. Company data comes from your own files, or from licensed or official provider
APIs (see [integrations](integrations/README.md)).

---

## 1. Manual entry

1. Open **Companies** and choose **Add one**.
2. Enter the company **name**. Add a **website** and/or a **profile URL**. At least one of the two
   is needed, because that is how duplicates are found.
3. Choose **Add**. The company appears in the list as *Not analyzed*.

Run the analysis from the company page (**Run analysis**) or in bulk from the list
(**Analyze N companies**, or tick rows and use **Analyze** in the bar).

Through the API: `POST /api/v1/campaigns/{id}/companies/` with `name`, `website` and/or
`profile_url`, optional `country`, and `analyze: true` to queue the analysis at once.

---

## 2. CSV upload

### The file

Download the template from the import page, or use
[`docs/samples/companies-template.csv`](samples/companies-template.csv). Columns:

| Column | Required | Notes |
| --- | --- | --- |
| `company_name` | yes | Any language, including Arabic. Up to 2,000 characters. |
| `website` | one of these two | `example.com` or `https://example.com/about`: the domain is what counts. |
| `profile_url` | one of these two | A full URL, for example a LinkedIn company page. Used as an identifier only. |
| `country` | no | Two-letter ISO code (`SA`) or a country name. |
| `industry`, `notes` | no | In the template for your own use. They are **not imported yet** and are ignored. |

Your own column names work too: the import screen suggests a mapping from the headers and lets you
change it before importing.

Limits: **10 MiB**, **5,000 rows**, **50 columns**, **2,000 characters per cell**. Encodings:
UTF-8 (with or without the Excel BOM) and UTF-16 are read directly; a legacy single-byte file
(Windows-1252) is read too, with a warning to check that accents look right. Save from Excel as
*CSV UTF-8*.

### Steps

1. **Import a CSV**, then pick the file (or drop it). Nothing is stored yet.
2. Check the **preview**: the mapping, the first rows, and any problems it found.
3. **Start the import.** It runs in the background; the page shows progress.
4. When it finishes you see how many rows were **added**, **already there** (duplicates),
   **restored** and **could not be added**. Use **Download the rows with problems** for a CSV that
   lists each failed row with the reason.
5. The new companies are in **Companies**, ready to analyze.

A bad row never stops the import. You can **cancel** a running import: rows already added stay.
Uploading the very same file again while the first copy is still running is refused; once it has
finished the same file can be imported again (every row then comes back as a duplicate).

---

## 3. Find companies at a provider

1. Choose **Find from a provider** (Companies page or the CSV import page).
2. The first search runs with **the campaign's own filters** (countries, industries, size,
   business model). The filters are shown in the boxes and you can edit them and search again.
3. The table marks every result: **New**, **Already in this campaign**, **Looks like one you
   have**, **Imported before** or **Cannot be imported**. Companies that already exist are not
   ticked and, when they cannot be imported, cannot be ticked.
4. Tick what you want (new companies are ticked for you), then **Import N companies**. At most the
   provider's selection limit (100) can be imported at once.
5. A small selection finishes at once; a larger one runs in the background. Either way you land on
   the same summary as for a CSV.

If no provider can be searched, the page says so and links to **Integrations**. In development
the offline `fake` provider is available (`PROVIDER_FAKE_ENABLED`, on by default in dev), so the
whole flow can be tried without any account.

---

## What happens to each company

Every row of a CSV or every ticked provider result ends in exactly one outcome:

| Outcome | Meaning |
| --- | --- |
| **created** | A new company was added to the campaign. |
| **duplicate** | The company is already in the campaign. Nothing is changed; the existing record is left as it is. |
| **restored** | The company had been archived. It is brought back, with its history. |
| **skipped** | Not processed (for example the batch was cancelled first). |
| **failed** | The row could not be added; see the reason in the problem file. |

### How duplicates are found

In this order, inside one campaign:

1. **Website domain.** `https://www.Acme.com/about?x=1` and `acme.com` are the same company.
   (Strong match.)
2. **Profile URL.** The same profile page is the same company. (Strong match.)
3. **Name plus country**, only when there is no strong match and nothing contradicts it: two
   companies with different domains are never merged because their names look alike. A weak
   match is reported as *possible duplicate*; the company is still added, flagged.

The same domain in another campaign or for another client is a different company row, by design.

---

## For developers

### The batch model

Every import creates one `ImportBatch` (`apps/imports/models.py`) with a `source` of `manual`,
`csv` or `provider`, and one `ImportRow` per input row with its `outcome` and, for failures, an
`error_code`. The batch holds the counters (`total`, `created`, `duplicate`, `restored`, `skipped`,
`failed`; a database check keeps `processed` from exceeding `total`) and a status: `pending`,
`processing`, then `completed` (no failed rows), `partial` (some failed) or `failed`, or
`cancelled`. Rows are written through `apps/imports/services.py`, which keeps the counters right.

### How a CSV import runs

`POST /api/v1/campaigns/{id}/csv-import/` validates the whole file (the same rules as the
preview), stores it under a random name in a private directory, creates the batch and a job, and
answers `202` at once. A Django-Q2 worker (`apps/imports/tasks.py`, `csv_import.run_csv_import`)
reads the file in a stream and records rows in chunks. It is **idempotent and resumable**: rows
already recorded are skipped, so a redelivered task or a retry after a crash carries on and never
duplicates a company. The file is deleted when the batch ends. Other code: `csvparse.py` (parsing,
mapping, limits), `schema.py` (field validation and its error codes), `error_report.py` (the
problem file), `uploads.py` (storage and cleanup).

### Provider imports

A provider is a `CompanySearchProvider` (`apps/imports/providers`). `provider_import.py` builds the
search from the campaign (`default_search_request`), annotates every result with its duplicate
status, and imports the selection through the same row pipeline as a CSV. Adding a provider means
adding an adapter that returns `ProviderCompany` results; the service and the screen need no
change. See [integrations](integrations/README.md).

### Hooking analysis to new companies

Analysis is **not** started automatically for imported companies; it costs money per company, so
it stays an explicit step:

* **Manual entry:** pass `analyze: true`, which calls `request_analysis(company, user)`.
* **After an import:** call `POST /api/v1/campaigns/{id}/accounts/analyze/` with `scope:
  "unassessed"` (everything never analyzed) or `scope: "ids"` with `company_ids`. The Companies
  page does this with **Analyze N companies** and the bulk bar.
* To analyze every new company automatically, call `request_analysis` from
  `csv_import._handle_row` / the provider import for rows whose outcome is `created`, behind a
  campaign setting and the usage budget (see [LLM usage](integrations/llm.md)).

### Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `IMPORT_UPLOAD_DIR` | `backend/var/import-uploads` | Where uploaded files wait for the worker. Must be shared by the api and the worker. |
| `IMPORT_CHUNK_SIZE` | `100` | Rows per progress save. |
| `IMPORT_MAX_ACTIVE_BATCHES` | `3` | CSV and provider batches of one campaign that may run at once. |
| `API_THROTTLE_CSV_UPLOAD` | `60/hour` | Previews and uploads per user. |
| `API_THROTTLE_PROVIDER_SEARCH` | `300/hour` | Provider searches per user. |
| `API_THROTTLE_PROVIDER_IMPORT` | `60/hour` | Provider imports per user. |
| `PROVIDER_FAKE_ENABLED` | on in dev | The offline test provider. |

### Performance

Measured on a developer laptop with the database in Docker: about **30 ms per row** for 500 rows
(15 seconds), 45 ms per row for 2,000 rows (90 seconds) and 82 ms per row for a full **5,000-row
file (411 seconds)**, using about 1 MiB of extra memory because the file is streamed. The cost is
roughly 18 small database operations per row (company, audit trail, row result, locked counters),
and it grows faster than the row count. A file near the 5,000-row limit can therefore run longer
than the worker's task timeout (`Q_TASK_TIMEOUT`, default 300 seconds). That is safe: the task is resumable and is retried after
`Q_TASK_RETRY`, carrying on where it stopped. For regular big files raise `Q_TASK_TIMEOUT`. The
tests keep two budgets: 500 rows in every run, and 5,000 rows on request
(`IMPORT_PERF_5000=1 pytest tests/test_import_scale.py -k 5000 -s`). Running the task against a
real `qcluster` worker is a manual check: start the stack, upload a 500-row file and watch the
batch reach `completed`.

---

## Security notes

* **Permissions.** Every import endpoint needs the *manager* or *admin* level on the campaign's
  client. A campaign or batch of a client you do not belong to answers **404**, the same as one
  that does not exist, so ids cannot be probed. Tests cover two clients on every route.
* **Throttling and limits.** Previews, uploads, provider searches and provider imports are
  throttled **per user** (`429 throttled`, with `retry_after`; the three rates above are
  settings). A campaign may have only `IMPORT_MAX_ACTIVE_BATCHES` CSV or provider batches
  pending or processing at once (`429 too_many_active_imports`); manual entries are not counted.
* **Upload validation (on the server).** The file name and the `Content-Type` are not trusted.
  The content is checked: the size is capped (a body over the limit is refused before it is
  read), spreadsheets, zips, PDFs and executables are recognized by their header and refused
  (`unsupported_format`), other binary data is refused (`binary_file`), and text that cannot be
  decoded is refused (`undecodable`). So a non-CSV renamed `.csv` is rejected.
* **Storage.** The file is saved under a random name (the batch id), never derived from the
  user's file name, in a private directory (mode 0700, files 0600) that is never served over HTTP,
  outside the web root. It is deleted when its batch ends. `manage.py cleanup_import_uploads
  [--max-age-hours 24]` removes anything a crash left behind (files of ended or unknown batches, or
  older than the cut-off); run it from cron.
* **Text from the input.** Zero-width and bidirectional-control characters and the BOM are
  removed, control characters are refused, and surrounding spaces are trimmed. Values are stored
  as typed otherwise, because a company name can legitimately start with `=`. Anything that
  leaves the system as a CSV (the problem file, the approved-accounts export) has a leading `'`
  put in front of a value that starts with `=`, `+`, `-`, `@` (also the full-width forms) or a
  tab or carriage return, so a spreadsheet cannot run it as a formula.
* **Logs.** Logs carry batch and job ids and counts only: never file names, cell values, company
  names or provider keys. A test pins this. Provider error text from upstream is never stored or
  shown, only a fixed message and code.

---

## Troubleshooting

Every error has a stable `error.code`. File-level problems are `400` unless stated, with
`error.details.issues` listing every problem found.

### The file or the mapping (CSV)

| Code | What it means | What to do |
| --- | --- | --- |
| `empty_file` | The file has no content. | Check you chose the right file. |
| `no_header` | There is no header row. | The first row must name the columns. |
| `no_data_rows` | A header but no data rows. | Add rows below the header. |
| `file_too_large` (413) | Over 10 MiB. | Split the file. |
| `too_many_rows` | Over 5,000 rows. | Split the file into parts of up to 5,000. |
| `too_many_columns` | Over 50 columns. | Delete columns you do not need. |
| `header_too_long` | A column title is very long. | Shorten the header. |
| `binary_file` | The file contains binary data. | It is not a text CSV; export again as CSV. |
| `unsupported_format` | A spreadsheet, zip, PDF or similar. | Save it as CSV (UTF-8) from the program that made it. |
| `undecodable` | The text cannot be decoded. | Save as *CSV UTF-8* and try again. |
| `name_column_missing` | No column is mapped to the company name. | Choose the name column in the mapping. |
| `unknown_column` | The mapping names a column that is not in the file. | Re-choose the mapping from the file's headers. |
| `unknown_field` | The mapping targets a field that does not exist. | Pick a field from the list. |
| `duplicate_target` | Two columns are mapped to the same field. | Map each field once. |

Warnings (shown in the preview, the import still works): `encoding_fallback` (the file is not
UTF-8: check that accents look right), `duplicate_headers` and `blank_headers` (renamed).

### One row (CSV rows and manual entry)

A row with a problem is recorded as **failed** with one of these codes, and never stops the import.

| Code | What it means | What to do |
| --- | --- | --- |
| `name_required` | The name is empty. | Fill in the name. |
| `name_too_long` | The name is over the limit. | Shorten it. |
| `identifier_required` | No website and no profile URL. | Add one of the two. |
| `website_invalid` | Not a valid domain (`not a url`, `http://`). | Use `example.com` or `https://example.com`. |
| `website_invalid_scheme` | Starts with something other than http or https. | Remove `ftp://` and similar. |
| `website_has_credentials` | The URL contains a user name or password. | Remove it. |
| `website_too_long`, `profile_url_too_long` | Over the length limit. | Shorten it. |
| `profile_url_invalid`, `profile_url_invalid_scheme`, `profile_url_has_credentials` | The profile URL is not a full, plain http(s) URL. | Use the full address from the browser. |
| `country_invalid_format` | Not a two-letter code. | Use `SA`, not `Saudi`. |
| `country_unknown` | Not an assigned ISO country code. | Check the code. |
| `invalid_characters` | Control characters in a value. | Remove them (often from a copy-paste). |
| `not_text`, `invalid_input` | The value was not text / not an object (API calls). | Send text values. |
| `column_count_mismatch` | The row has a different number of columns than the header. | Check for an unquoted comma in a name. |
| `cell_too_long` | A cell is over 2,000 characters. | Shorten it. |
| `malformed_row` | Broken quoting. | Close every quote. |

### The import itself (any path)

| Code | HTTP | What it means | What to do |
| --- | --- | --- | --- |
| `import_in_progress` | 409 | The same file is already being imported into this campaign. | Wait for it, or cancel it. |
| `too_many_active_imports` | 429 | The campaign already has the most imports allowed at once. | Wait for one to finish or cancel one. |
| `throttled` | 429 | You are going too fast (`details.retry_after` seconds). | Wait and try again. |
| `validation_error` | 400 | A field was wrong (archived campaign or client, a bad filter). | Read `details`. |
| `permission_denied` | 403 | Your role cannot import. | Ask a manager or admin. |
| `not_found` | 404 | The campaign or batch does not exist, or is not yours. | Check the campaign in the header. |

### The provider

| Code | HTTP | What it means |
| --- | --- | --- |
| `provider_rate_limited` | 429 | The provider asked us to slow down; `details.retry_after`. |
| `provider_unavailable` | 503 | The provider is down or not answering. Try again later. |
| `provider_auth` | 502 | Our credentials for the provider are wrong. Fix them under Integrations. |
| `provider_quota_exceeded` | 502 | The provider's quota or credits are used up. |
| `provider_bad_request` | 400 | A filter the provider cannot use (`details.field`). |
| `provider_error` | 502 | Any other provider failure. |

### Common situations

* **"Everything came back as a duplicate."** The same file or the same companies were imported
  before. Nothing was changed; that is the intended behavior.
* **"The batch seems stuck."** Check that the worker is running (`docker compose ps`, the
  `worker` service). A big file can also take minutes; the progress bar moves in chunks.
* **"A name shows with a leading apostrophe in my spreadsheet."** That is the formula guard on
  exports (see Security notes); the stored name is unchanged.
