# Review queue and bulk decisions

`/companies/review` lists the accounts that have a recommendation and no human decision yet, one
card at a time. Deciding never sends anything: it only records the decision (and a timeline
entry).

| Key | Action |
|---|---|
| A / H / S | Add, hold, skip, then move to the next account |
| J or right arrow / K or left arrow | Next / previous account |
| O | Open the full card |
| N | Write an optional note (shortcuts are off while you type) |
| ? | Help |

The queue is the server's "needs review" list, so reloading the page resumes where you left off.
The next card is loaded ahead of time.

## Bulk API

`POST /api/v1/campaigns/{id}/accounts/decide/` takes up to 200 `company_ids`, a `decision` and an
optional `note`. Every account is handled alone and reported as `saved`, `unchanged` (same
decision already stands and no new note) or `error`, so repeating a request changes nothing and
one bad account never blocks the rest. Needs the reviewer level.

Not built yet: overriding the primary or secondary buyer from the queue (use the card's people
section) and a bulk-decision button on the dashboard table.
