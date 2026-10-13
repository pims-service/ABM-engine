# Headcount growth

How the research engine turns headcount observations into the 3, 6 and 12 month growth figures of Brief section 5, and into a plain-language explanation. The code is `backend/apps/research/growth/`: pure Python, `Decimal` arithmetic, no database, no network.

Two rules shape everything here:

- **Unknown stays unknown.** A growth figure is `None` when we cannot measure it. We never guess, round a gap away or fill in a number.
- **Every figure says why** (Brief section 15). Each result carries its two end points and the source of each, and `explain()` turns that into a sentence. There is no score.

## Inputs: headcount points

A `HeadcountPoint` is one observation: a `date`, a `count`, a `source` string for provenance (for example `provider:pdl`, `company:about-page`, `manual:analyst`) and an optional `department`.

`build_series` (or `group_points`, which splits mixed points by department first) cleans a list of points before any maths:

- sorts by date and removes same-day duplicates, keeping the most trusted source (`manual:` then `filing:` then `company:` then `provider:`, configurable);
- rejects negative counts, counts above `max_headcount`, and dates after `as_of`;
- reports everything it rejected in `GrowthReport.rejected`, so a bad input is visible rather than silently dropped.

## Computing a change

`compute_change(series, months, as_of=...)` returns a `GrowthResult`:

```
percent = (latest - baseline) / baseline * 100      # Decimal, 2 places, ROUND_HALF_UP
```

- The **baseline** is the point closest to `as_of - months`, within a tolerance window (defaults: 21 days for 3 months, 30 for 6, 45 for 12).
- If no real point is close enough, the baseline may be **interpolated** between the two surrounding points, but only when they are at most `max_gap_days` apart (default 120). The result says `method: interpolated` and shows both neighbours.
- The **latest** point must be at most `latest_max_age_days` old at `as_of` (default 45).
- Month arithmetic is calendar-safe: `2026-03-31` minus one month is `2026-02-28`.

Every result has a `status`:

| Status | Meaning |
| --- | --- |
| `ok` | Measured normally. |
| `low_base` | Measured, but the baseline is under 10 employees (default). A rise from 1 to 2 is +100% and should not be read as strong growth. |
| `insufficient_data` | No usable baseline or latest point. `percent` is `None` and `reason` says why, for example "no headcount point within 30 days of 2026-04-13". |
| `undefined_baseline` | The baseline is zero, so a percentage does not exist. |
| `out_of_range` | The percent is beyond the stored column range (`+-9999.99`). It is flagged, never clamped. |

Providers sometimes return a ready-made growth percentage instead of a time series. `provider_result` wraps such a value as `method: provider`, keeping the source, so it flows through the same report.

## Departments and the commercial team

`compute_growth` produces company-level results and one result per department. Department names are normalised (`bd`, `business development`, `partnerships`, `commercial`, `sales`, `marketing`, with Arabic synonyms where they are obvious).

The **commercial team** roll-up (sales, business development, marketing and commercial/partnerships) is computed from summed series. It is only produced when at least `commercial_min_components` departments (default 2) have points at comparable dates (within `commercial_align_days`, default 14). Otherwise it is `insufficient_data` with the reason.

## Explaining the result

`explain(report)` returns one sentence per available result, for example:

> Headcount fell 12.00% over 12 months (from 176 on 2025-10-13 to 155 on 2026-10-13, source: provider:pdl).

Interpolated baselines and `low_base` results say so in the sentence. Results that could not be measured say why.

## Storing the result

`to_research_fields(report)` maps a report onto the `CompanyResearch` columns:

- `headcount_change_3m`, `headcount_change_6m`, `headcount_change_12m`: the company-level percent as `Decimal`, or `None` when unknown (matching the `Decimal(6,2)` columns).
- `department_growth`: a JSON document, version 1, with one block per window for the company, each department and the commercial team. The exact shape is in the docstring of `apps/research/growth/fields.py`. It is `None` when every result is `insufficient_data`, so "we looked and found nothing" is distinguishable from data.

## Trigger hint (for the signal engine)

`GrowthTriggerHint` answers "is this growth at least this big?" for the two growth signal types, `headcount_growth` and `commercial_team_growth`. It creates no signal and judges no ICP fit: the qualification milestone owns that (Brief section 2: a company does not need a trigger to qualify).

The defaults are a starting point, not policy, and all of them are parameters:

- window: 6 months;
- at least `+10%` **and** an absolute change of at least 5 employees.

Requiring both stops a tiny company (one hire is +50%) and a huge one (a few hires are +0.1%) from triggering on noise. Unknown growth never meets a threshold. The hint returns `(met, evidence sentence)`.

## Configuration

All tunables live in `GrowthConfig` (`tolerance_days`, `max_gap_days`, `latest_max_age_days`, `low_base_threshold`, `max_headcount`, `commercial_min_components`, `commercial_align_days`, `source_priority`). `DEFAULT_CONFIG` holds the defaults above.

## How a collector uses it

```python
from apps.research.growth import growth_from_points, explain, to_research_fields

report = growth_from_points(points, as_of=today)   # points: list of HeadcountPoint
sentences = explain(report)                         # show these next to the figures
fields = to_research_fields(report)                 # store on the research snapshot
```
