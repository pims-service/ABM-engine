# Trigger windows

A **trigger** is a reason to contact a company now (Brief section 7). A signal only counts as a
current trigger for a limited time, so a stale event does not stay "current" for ever. Each kind of
signal has a default window; a campaign can set its own.

## Defaults

Days after the event date (`apps/research/windows.py`, `FRESH_DAYS`):

| Signal | Days |
| --- | --- |
| Sales, BD, commercial, partnerships hiring | 60 |
| Headcount growth, commercial team growth | 120 |
| Funding | 365 |
| Market expansion, new leadership, new office, new product, major partnership | 180 |
| Other | 90 |

A signal is a current trigger while its event date plus the window is after now. The event day
starts at midnight UTC, and the signal goes stale at the instant its window ends.

## Setting a campaign's windows

`GET /campaigns/{id}/trigger-windows/` returns the campaign's own windows, the default of every
type and the longest allowed (3650 days). `PUT` replaces the campaign's windows:

```json
{"windows": {"funding": 180, "sales_hiring": 30}}
```

A type that is not listed keeps its default; `{}` goes back to all defaults. Each value is 1 to
3650 whole days; the answer names every bad type. Editing needs the manager role, and an archived
campaign is read-only. Every change is written to the audit log.

## How it is applied

Windows are applied when trigger status is read, not stored on the signals. Signals are
append-only and are never rewritten, so a changed window applies to the signals already stored at
once, and an expired signal stays stored and visible (it just does not count as a trigger). The
dashboard's Trigger filter and count, the company card, the CRM push and the approved-accounts
export all use the campaign's windows. The card shows a signal's expiry by the window in force.

There is no nightly job: because nothing is stored, there is nothing to refresh. For a type with a
campaign window, the signal's own stored expiry is ignored, even for a signal with no stored
expiry, so a campaign window also puts an end to such a signal's "fresh until a rule exists".

## Not built

A screen to edit the windows: use the API. The windows are not part of a campaign's versioned
rules, so changing them does not make a new rules version and does not change the rules hash.
