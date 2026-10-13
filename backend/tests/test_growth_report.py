# ruff: noqa: S311
"""Headcount growth: report, commercial roll-up, explanations, field mapping, triggers (#97)."""

from __future__ import annotations

import ast
import datetime as dt
import json
import random
from decimal import Decimal
from pathlib import Path

import pytest

import apps.research.growth as growth
from apps.research.growth import (
    COMMERCIAL_TEAM_GROWTH,
    DEPARTMENT_ORDER,
    HEADCOUNT_GROWTH,
    GrowthConfig,
    GrowthMethod,
    GrowthStatus,
    GrowthTriggerHint,
    HeadcountPoint,
    ProviderGrowth,
    build_series,
    compute_change,
    compute_growth,
    explain,
    explain_result,
    growth_from_points,
    normalize_department,
    shift_months,
    to_research_fields,
)

AS_OF = dt.date(2026, 10, 13)


def d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def pt(day: str, count: int, source: str = "provider:x", dept: str | None = None) -> HeadcountPoint:
    return HeadcountPoint(d(day), count, source, dept)


def at_months(
    counts: dict[int, int],
    *,
    dept: str | None = None,
    source: str = "provider:x",
    as_of: dt.date = AS_OF,
    lag_days: int = 0,
) -> list[HeadcountPoint]:
    """Points ``m`` months before ``as_of`` (then ``lag_days`` earlier)."""
    return [
        HeadcountPoint(shift_months(as_of, -m) - dt.timedelta(days=lag_days), n, source, dept)
        for m, n in counts.items()
    ]


def ser(points: list[HeadcountPoint], dept: str | None = None, as_of: dt.date = AS_OF):
    return build_series(points, as_of=as_of, department=dept)


# ----------------------------------------------------------------- departments


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sales", "sales"),
        ("  Sales  ", "sales"),
        ("SALES TEAM", "sales"),
        ("المبيعات", "sales"),
        ("bd", "bd"),
        ("Business Development", "bd"),
        ("business-development", "bd"),
        ("Biz Dev", "bd"),
        ("تطوير الأعمال", "bd"),
        ("Marketing", "marketing"),
        ("التسويق", "marketing"),
        ("commercial", "commercial"),
        ("Commercial & Partnerships", "commercial"),
        ("التجاري", "commercial"),
        ("Partnerships", "partnerships"),
        ("partners", "partnerships"),
        ("الشراكات", "partnerships"),
        ("Engineering", None),
        ("Sales & Marketing", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_department(name, expected):
    assert normalize_department(name) == expected


def test_every_canonical_department_matches_the_framework_vocabulary():
    from apps.integrations.framework.normalized import DEPARTMENTS, SIGNAL_TYPES

    assert set(DEPARTMENT_ORDER) == DEPARTMENTS
    assert {HEADCOUNT_GROWTH, COMMERCIAL_TEAM_GROWTH} <= SIGNAL_TYPES
    for name in DEPARTMENT_ORDER:
        assert normalize_department(name) == name


# ----------------------------------------------------------------- the report


def full_report():
    company = ser(at_months({0: 88, 3: 92, 6: 96, 12: 100}, source="provider:apollo"))
    sales = ser(at_months({0: 30, 3: 28, 6: 26, 12: 20}, dept="sales"), "sales")
    marketing = ser(
        at_months({0: 10, 3: 10, 6: 12, 12: 15}, dept="marketing", lag_days=5), "marketing"
    )
    return compute_growth({None: company, "sales": sales, "marketing": marketing}, AS_OF)


def test_company_windows_and_months():
    report = full_report()
    assert report.company.m3.percent == Decimal("-4.35")
    assert report.company.m6.percent == Decimal("-8.33")
    assert report.company.m12.percent == Decimal("-12.00")
    assert report.company.get(12) is report.company.m12
    assert report.as_of == AS_OF
    assert [r.months_requested for r in report.company.results()] == [3, 6, 12]
    assert report.rejected == ()
    assert report.ignored_departments == ()


def test_department_results_come_in_canonical_order():
    company = ser(at_months({0: 88, 12: 100}))
    sales = ser(at_months({0: 30, 12: 20}, dept="sales"), "sales")
    marketing = ser(at_months({0: 10, 12: 15}, dept="marketing"), "marketing")
    report = compute_growth({"marketing": marketing, None: company, "SALES": sales}, AS_OF)
    assert list(report.departments) == ["sales", "marketing"]
    assert report.departments["sales"].m12.percent == Decimal("50.00")
    assert report.departments["marketing"].m12.percent == Decimal("-33.33")
    assert report.departments["sales"].m12.scope == "sales"
    assert len(report.all_results()) == 3 * 4


def test_commercial_team_sums_aligned_components():
    report = full_report()
    team = report.commercial_team
    # latest: sales 30 (2026-10-13) + marketing 10 (2026-10-08); 12m baseline 20 + 15
    assert team.m12.percent == Decimal("14.29")  # (40 - 35) / 35
    assert team.m12.components == ("sales", "marketing")
    assert team.m12.scope == "commercial_team"
    assert team.m12.from_point.count == Decimal(35)
    assert team.m12.to_point.count == Decimal(40)
    assert team.m12.to_point.date == AS_OF  # dated at the latest component point
    assert team.m3.percent == Decimal("5.26")  # (40 - 38) / 38
    assert team.m6.percent == Decimal("5.26")
    assert team.m12.from_point.source == "provider:x"


def test_commercial_team_roll_up_needs_two_departments():
    only_sales = compute_growth(
        {"sales": ser(at_months({0: 30, 12: 20}, dept="sales"), "sales")}, AS_OF
    )
    team = only_sales.commercial_team
    assert team.m6.status == GrowthStatus.INSUFFICIENT_DATA
    assert team.m6.percent is None
    assert "needs at least 2 departments with data; have 1 (sales)" in team.m6.reason
    nothing = compute_growth({}, AS_OF)
    assert "have 0" in nothing.commercial_team.m3.reason
    assert nothing.company.m3.reason == "no company-level headcount data"
    assert nothing.departments == {}
    relaxed = GrowthConfig(commercial_min_components=1)
    one = compute_growth(
        {"sales": ser(at_months({0: 30, 12: 20}, dept="sales"), "sales")}, AS_OF, config=relaxed
    )
    assert one.commercial_team.m12.percent == Decimal("50.00")


def test_commercial_team_needs_comparable_dates():
    sales = ser(at_months({0: 30, 12: 20}, dept="sales"), "sales")
    marketing = ser(at_months({0: 10, 12: 15}, dept="marketing", lag_days=60), "marketing")
    report = compute_growth({"sales": sales, "marketing": marketing}, AS_OF)
    for result in report.commercial_team.results():
        assert result.status == GrowthStatus.INSUFFICIENT_DATA
        assert result.percent is None
        assert result.reason == (
            "no dates where sales, marketing all have a headcount point within 14 days of "
            "each other"
        )
    wide = GrowthConfig(commercial_align_days=61)
    assert compute_growth(
        {"sales": sales, "marketing": marketing}, AS_OF, config=wide
    ).commercial_team.m12.is_available


def test_commercial_team_alignment_picks_the_tightest_combination():
    as_of = d("2026-01-10")
    # Two sales points near one marketing point: (01-03, 01-10) has spread 7, (01-10, 01-10) 0.
    sales = ser(
        [
            pt("2025-07-10", 90, dept="sales"),
            pt("2026-01-03", 100, dept="sales"),
            pt("2026-01-10", 110, dept="sales"),
        ],
        "sales",
        as_of,
    )
    marketing = ser(
        [pt("2025-07-10", 10, dept="marketing"), pt("2026-01-10", 10, dept="marketing")],
        "marketing",
        as_of,
    )
    result = compute_growth({"sales": sales, "marketing": marketing}, as_of).commercial_team.m6
    assert result.to_point.count == Decimal(120)
    assert result.percent == Decimal("20.00")
    # The same two candidates found in the opposite order (marketing anchors come last).
    sales = ser(
        [pt("2025-07-10", 90, dept="sales"), pt("2026-01-10", 110, dept="sales")], "sales", as_of
    )
    marketing = ser(
        [
            pt("2025-07-10", 10, dept="marketing"),
            pt("2026-01-03", 8, dept="marketing"),
            pt("2026-01-10", 10, dept="marketing"),
        ],
        "marketing",
        as_of,
    )
    result = compute_growth({"sales": sales, "marketing": marketing}, as_of).commercial_team.m6
    assert result.to_point.count == Decimal(120)
    assert result.percent == Decimal("20.00")


def test_commercial_team_includes_every_present_department():
    series = {name: ser(at_months({0: 10, 12: 8}, dept=name), name) for name in DEPARTMENT_ORDER}
    team = compute_growth(series, AS_OF).commercial_team.m12
    assert team.components == DEPARTMENT_ORDER
    assert team.percent == Decimal("25.00")
    assert team.to_point.count == Decimal(50)
    assert team.to_point.source == "provider:x"


def test_departments_with_synonym_keys_are_merged():
    one = ser(at_months({0: 10}, dept="bd"), "bd")
    two = ser(at_months({12: 8}, dept="bd"), "bd")
    dup = ser([pt("2026-10-13", 99, "provider:z", dept="bd")], "bd")
    report = compute_growth({"bd": one, "Business Development": two, "تطوير الأعمال": dup}, AS_OF)
    assert report.departments["bd"].m12.percent == Decimal(
        "25.00"
    )  # same-day tie: the alphabetically first source (10, not 99)
    assert len(report.rejected) == 1
    assert "duplicate" in report.rejected[0].reason


def test_unknown_department_keys_are_reported_not_used():
    report = compute_growth(
        {None: ser(at_months({0: 5, 12: 4})), "Legal": ser([])},
        AS_OF,
        provider={"Finance": ProviderGrowth("p", {3: Decimal(1)})},
    )
    assert report.ignored_departments == ("Legal", "Finance")
    assert report.departments == {}


def test_growth_from_points_groups_builds_and_reports_rejects():
    points = [
        *at_months({0: 88, 12: 100}),
        *at_months({0: 30, 12: 20}, dept="Sales"),
        *at_months({0: 10, 12: 15}, dept="التسويق"),
        pt("2027-01-01", 5),  # future
        pt("2026-01-01", 5, dept="Legal"),
        pt("2026-01-01", -4, dept="Sales"),
    ]
    report = growth_from_points(points, AS_OF)
    assert report.company.m12.percent == Decimal("-12.00")
    assert report.commercial_team.m12.components == ("sales", "marketing")
    assert sorted(r.reason.split(" ")[0] for r in report.rejected) == [
        "date",
        "negative",
        "unknown",
    ]


# ----------------------------------------------------------------- provider fallback


def test_provider_percentages_fill_only_what_the_series_cannot_answer():
    company = ser(at_months({0: 88, 3: 92}))
    provider = {
        None: ProviderGrowth(
            "provider:apollo", {3: Decimal("-1.00"), 6: Decimal("-7.5"), 12: None}
        ),
        "sales": ProviderGrowth("provider:apollo", {12: Decimal("4.2")}),
    }
    report = compute_growth({None: company}, AS_OF, provider=provider)
    assert report.company.m3.method == GrowthMethod.OBSERVED  # computed wins over provider
    assert report.company.m3.percent == Decimal("-4.35")
    assert report.company.m6.method == GrowthMethod.PROVIDER
    assert report.company.m6.percent == Decimal("-7.50")
    assert report.company.m12.status == GrowthStatus.INSUFFICIENT_DATA
    assert report.departments["sales"].m12.percent == Decimal("4.20")
    assert report.departments["sales"].m3.percent is None
    assert report.departments["sales"].m3.reason == "no sales headcount data"


def test_provider_does_not_override_a_zero_baseline():
    company = ser([pt("2025-10-13", 0), pt("2026-10-13", 5)])
    report = compute_growth(
        {None: company}, AS_OF, provider={None: ProviderGrowth("p", {12: Decimal(50)})}
    )
    assert report.company.m12.status == GrowthStatus.UNDEFINED_BASELINE


# ----------------------------------------------------------------- explanations


def test_explain_snapshot_company_and_departments():
    lines = explain(full_report())
    assert lines[:3] == (
        "Headcount fell 4.35% over 3 months (from 92 on 2026-07-13 to 88 on 2026-10-13, "
        "change -4, source: provider:apollo).",
        "Headcount fell 8.33% over 6 months (from 96 on 2026-04-13 to 88 on 2026-10-13, "
        "change -8, source: provider:apollo).",
        "Headcount fell 12.00% over 12 months (from 100 on 2025-10-13 to 88 on 2026-10-13, "
        "change -12, source: provider:apollo).",
    )
    assert lines[3] == (
        "Sales headcount grew 7.14% over 3 months (from 28 on 2026-07-13 to 30 on 2026-10-13, "
        "change +2, source: provider:x)."
    )
    assert lines[6].startswith("Marketing headcount was unchanged over 3 months")
    assert lines[9] == (
        "Commercial team headcount (sales + marketing) grew 5.26% over 3 months "
        "(from 38 on 2026-07-13 to 40 on 2026-10-13, change +2, source: provider:x)."
    )
    assert len(lines) == 12


def test_explain_unknown_results_say_why():
    report = compute_growth({None: ser([pt("2026-10-13", 100)])}, AS_OF)
    lines = explain(report)
    assert lines[0] == (
        "Headcount change over 3 months is unknown: no headcount point within 21 days of "
        "2026-07-13."
    )
    assert lines[2].endswith("within 45 days of 2025-10-13.")
    assert lines[3].startswith("Commercial team headcount change over 3 months is unknown: ")
    assert all("unknown" in line for line in lines)


def test_explain_zero_baseline_out_of_range_and_low_base():
    zero = compute_change(
        ser([pt("2025-10-13", 0, "s"), pt("2026-10-13", 5, "s")]), 12, as_of=AS_OF
    )
    assert explain_result(zero) == (
        "Headcount change over 12 months has no usable percentage: baseline headcount on "
        "2025-10-13 is 0, so a percentage is undefined. It went from 0 on 2025-10-13 to 5 on "
        "2026-10-13 (change +5, source: s)."
    )
    huge = compute_change(
        ser([pt("2025-10-13", 10, "s"), pt("2026-10-13", 100000, "t")]), 12, as_of=AS_OF
    )
    assert explain_result(huge) == (
        "Headcount change over 12 months has no usable percentage: change of 999900.00% is "
        "outside the storable range (+-9999.99%). It went from 10 on 2025-10-13 to 100000 on "
        "2026-10-13 (change +99990, sources: s; t)."
    )
    small = compute_change(
        ser([pt("2025-10-13", 2, "s"), pt("2026-10-13", 4, "s")]), 12, as_of=AS_OF
    )
    assert explain_result(small) == (
        "Headcount grew 100.00% over 12 months (from 2 on 2025-10-13 to 4 on 2026-10-13, "
        "change +2, source: s). Caution: baseline of 2 employees is below 10; a few hires "
        "look like large growth."
    )


def test_explain_interpolated_and_off_window_baselines():
    built = ser(
        [pt("2026-02-13", 200, "a"), pt("2026-06-13", 240, "b"), pt("2026-10-13", 250, "b")]
    )
    text = explain_result(compute_change(built, 6, as_of=AS_OF))
    assert text == (
        "Headcount grew 13.81% over 6 months (from an estimated 219.7 on 2026-04-13 "
        "(interpolated between 200 on 2026-02-13 and 240 on 2026-06-13) to 250 on 2026-10-13, "
        "change +30.3, sources: a, b; b)."
    )
    far = compute_change(
        ser([pt("2025-09-01", 100, "s"), pt("2026-10-13", 110, "s")]), 12, as_of=AS_OF
    )
    assert "covers 13.39 months" in explain_result(far)


def test_explain_provider_and_singular_month():
    report = compute_growth(
        {},
        AS_OF,
        provider={None: ProviderGrowth("provider:apollo", {3: Decimal("2.5"), 6: Decimal("-3")})},
    )
    assert explain(report)[0] == (
        "Headcount grew 2.50% over 3 months according to the provider "
        "(source: provider:apollo; dates and headcounts not supplied)."
    )
    assert explain(report)[1].startswith("Headcount fell 3.00% over 6 months according")
    one = compute_change(ser([pt("2026-09-13", 10), pt("2026-10-13", 10)]), 1, as_of=AS_OF)
    assert explain_result(one).startswith("Headcount was unchanged over 1 month (")


def test_explanations_contain_no_scores():
    for line in explain(full_report()):
        assert "score" not in line.lower()


# ----------------------------------------------------------------- research fields


def test_to_research_fields_columns_and_json_shape():
    fields = to_research_fields(full_report())
    assert set(fields) == {
        "headcount_change_3m",
        "headcount_change_6m",
        "headcount_change_12m",
        "department_growth",
    }
    assert fields["headcount_change_3m"] == Decimal("-4.35")
    assert fields["headcount_change_6m"] == Decimal("-8.33")
    assert fields["headcount_change_12m"] == Decimal("-12.00")
    blob = fields["department_growth"]
    assert json.loads(json.dumps(blob)) == blob  # JSON serialisable, so storable as is
    assert blob["version"] == 1
    assert blob["as_of"] == "2026-10-13"
    assert list(blob["departments"]) == ["sales", "marketing"]
    assert blob["commercial_team"]["components"] == ["sales", "marketing"]
    window = blob["company"]["12m"]
    assert window == {
        "months_requested": 12,
        "months_actual": "12.00",
        "status": "ok",
        "method": "observed",
        "percent": "-12.00",
        "absolute_change": "-12",
        "from": {
            "date": "2025-10-13",
            "count": "100",
            "source": "provider:apollo",
            "interpolated": False,
        },
        "to": {
            "date": "2026-10-13",
            "count": "88",
            "source": "provider:apollo",
            "interpolated": False,
        },
        "reason": "",
    }
    assert set(blob["departments"]["sales"]) == {"3m", "6m", "12m"}


def test_to_research_fields_null_means_unknown():
    empty = to_research_fields(compute_growth({}, AS_OF))
    assert empty == {
        "headcount_change_3m": None,
        "headcount_change_6m": None,
        "headcount_change_12m": None,
        "department_growth": None,
    }
    flagged = compute_growth({None: ser([pt("2025-10-13", 0), pt("2026-10-13", 5)])}, AS_OF)
    fields = to_research_fields(flagged)
    assert fields["headcount_change_12m"] is None  # not 0
    window = fields["department_growth"]["company"]["12m"]
    assert window["status"] == "undefined_baseline"
    assert window["percent"] is None
    assert window["absolute_change"] == "5"
    assert fields["department_growth"]["commercial_team"]["components"] == []


def test_to_research_fields_interpolated_and_fractional_values():
    built = ser([pt("2026-02-13", 200), pt("2026-06-13", 240), pt("2026-10-13", 250)])
    report = compute_growth({None: built}, AS_OF)
    window = to_research_fields(report)["department_growth"]["company"]["6m"]
    assert window["method"] == "interpolated"
    assert window["from"]["interpolated"] is True
    assert window["from"]["count"] == "219.67"  # fractional counts are shown to 2 places
    assert window["from"]["source"] == "provider:x"


def test_to_research_fields_values_fit_the_company_research_columns():
    from apps.companies.models import CompanyResearch

    for name in ("headcount_change_3m", "headcount_change_6m", "headcount_change_12m"):
        column = CompanyResearch._meta.get_field(name)
        assert getattr(column, "max_digits") == 6  # noqa: B009
        assert getattr(column, "decimal_places") == 2  # noqa: B009
        assert column.null
    assert CompanyResearch._meta.get_field("department_growth").null
    assert Decimal("9999.99") == growth.MAX_PERCENT


# ----------------------------------------------------------------- trigger hint


def test_trigger_met_when_both_thresholds_pass():
    report = compute_growth({None: ser(at_months({0: 84, 6: 75}))}, AS_OF)
    hint = GrowthTriggerHint().evaluate(report, HEADCOUNT_GROWTH)
    assert hint.met is True
    assert hint.evidence == (
        "Headcount changed 12.00% (+9 employees) over 6 months, meeting the threshold of "
        ">= 10% and >= 5 employees."
    )
    met, evidence = hint  # a plain (met, evidence) pair
    assert met is True
    assert evidence == hint.evidence


def test_trigger_not_met_below_percent_or_absolute_or_negative():
    hint = GrowthTriggerHint()
    low_percent = compute_growth({None: ser(at_months({0: 1040, 6: 1000}))}, AS_OF)
    assert hint.evaluate(low_percent, HEADCOUNT_GROWTH) == (
        False,
        "Headcount changed 4.00% (+40 employees) over 6 months, below the threshold of "
        ">= 10% and >= 5 employees.",
    )
    tiny = compute_growth({None: ser(at_months({0: 2, 6: 1}))}, AS_OF)
    result = hint.evaluate(tiny, HEADCOUNT_GROWTH)
    assert result.met is False  # +100% from 1 to 2 is not strong growth
    assert "(+1 employees)" in result.evidence
    shrinking = compute_growth({None: ser(at_months({0: 80, 6: 100}))}, AS_OF)
    assert hint.evaluate(shrinking, HEADCOUNT_GROWTH).met is False


def test_trigger_unknown_growth_is_never_met():
    report = compute_growth({None: ser([pt("2026-10-13", 100)])}, AS_OF)
    hint = GrowthTriggerHint().evaluate(report, HEADCOUNT_GROWTH)
    assert hint.met is False
    assert hint.evidence == (
        "Headcount growth over 6 months could not be assessed: no headcount point within "
        "30 days of 2026-04-13."
    )


def test_trigger_thresholds_are_parameters():
    report = compute_growth({None: ser(at_months({0: 1040, 12: 1000}))}, AS_OF)
    loose = GrowthTriggerHint(
        window_months=12, min_percent=Decimal("3"), min_absolute_change=Decimal("40")
    )
    assert loose.evaluate(report, HEADCOUNT_GROWTH).met is True
    strict = GrowthTriggerHint(
        window_months=12, min_percent=Decimal("3"), min_absolute_change=Decimal("41")
    )
    assert strict.evaluate(report, HEADCOUNT_GROWTH).met is False
    assert GrowthTriggerHint(window_months=3).window_months == 3


def test_trigger_commercial_team_signal():
    sales = ser(at_months({0: 30, 6: 20}, dept="sales"), "sales")
    marketing = ser(at_months({0: 12, 6: 10}, dept="marketing"), "marketing")
    report = compute_growth({"sales": sales, "marketing": marketing}, AS_OF)
    hint = GrowthTriggerHint().evaluate(report, COMMERCIAL_TEAM_GROWTH)
    assert hint.met is True
    assert hint.evidence.startswith("Commercial team headcount (sales + marketing) changed 40.00%")
    company_view = GrowthTriggerHint().evaluate(report, HEADCOUNT_GROWTH)
    assert company_view.met is False  # no company series: unknown, not borrowed from departments


def test_trigger_with_provider_percent_needs_absolute_change_unless_waived():
    report = compute_growth({}, AS_OF, provider={None: ProviderGrowth("p", {6: Decimal("25")})})
    hint = GrowthTriggerHint().evaluate(report, HEADCOUNT_GROWTH)
    assert hint.met is False
    assert "absolute change is unknown" in hint.evidence
    waived = GrowthTriggerHint(min_absolute_change=Decimal(0)).evaluate(report, HEADCOUNT_GROWTH)
    assert waived.met is True
    assert waived.evidence == (
        "Headcount changed 25.00% over 6 months, meeting the threshold of "
        ">= 10% and >= 0 employees."
    )


def test_trigger_rejects_bad_parameters():
    with pytest.raises(ValueError, match="window_months"):
        GrowthTriggerHint(window_months=4)
    with pytest.raises(TypeError, match="never float"):
        GrowthTriggerHint(min_percent=10.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="never float"):
        GrowthTriggerHint(min_absolute_change=5.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported signal type"):
        GrowthTriggerHint().evaluate(compute_growth({}, AS_OF), "funding")


# ----------------------------------------------------------------- property tests (seeded)


def month_points(rng: random.Random, as_of: dt.date, counts: list[int]) -> list[HeadcountPoint]:
    """One point per month-offset 0..12 (``counts[m]``), jittered a few days around the target."""
    out = []
    for m, n in enumerate(counts):
        jitter = -rng.randint(0, 3) if m == 0 else rng.randint(-3, 3)
        out.append(
            HeadcountPoint(shift_months(as_of, -m) + dt.timedelta(days=jitter), n, f"provider:s{m}")
        )
    return out


def percents(points: list[HeadcountPoint], as_of: dt.date) -> list[Decimal | None]:
    built = build_series(points, as_of=as_of)
    return [compute_change(built, m, as_of=as_of).percent for m in (3, 6, 12)]


@pytest.mark.parametrize("seed", range(40))
def test_monotone_series_gives_monotone_sign(seed):
    rng = random.Random(seed)
    steps = [rng.randint(0, 25) for _ in range(12)]
    start = rng.randint(20, 400)
    # oldest first: counts[m] for m = 12..0
    rising = [start]
    for step in steps:
        rising.append(rising[-1] + step)
    counts_up = list(reversed(rising))  # index m = months ago
    as_of = AS_OF + dt.timedelta(days=rng.randint(-2000, 2000))
    up = percents(month_points(rng, as_of, counts_up), as_of)
    assert all(p is not None and p >= 0 for p in up)
    falling = list(reversed(counts_up))
    down = percents(month_points(rng, as_of, falling), as_of)
    assert all(p is not None and p <= 0 for p in down)
    if sum(steps) > 0 and counts_up[0] > counts_up[12]:
        assert (up[2] or Decimal(0)) > 0
        assert (down[2] or Decimal(0)) < 0


@pytest.mark.parametrize("seed", range(40))
def test_shifting_dates_and_as_of_together_keeps_percent(seed):
    rng = random.Random(1000 + seed)
    counts = [rng.randint(15, 600) for _ in range(13)]
    shift = dt.timedelta(days=rng.randint(1, 900))
    points = month_points(rng, AS_OF, counts)
    moved = [HeadcountPoint(p.date + shift, p.count, p.source) for p in points]
    assert percents(points, AS_OF) == percents(moved, AS_OF + shift)
    assert None not in percents(points, AS_OF)


@pytest.mark.parametrize("seed", range(40))
def test_scaling_counts_keeps_percent(seed):
    rng = random.Random(2000 + seed)
    counts = [rng.randint(15, 600) for _ in range(13)]
    factor = rng.randint(2, 9)
    base = month_points(rng, AS_OF, counts)
    scaled = [HeadcountPoint(p.date, p.count * factor, p.source) for p in base]
    assert percents(base, AS_OF) == percents(scaled, AS_OF)


@pytest.mark.parametrize("seed", range(20))
def test_input_order_does_not_matter(seed):
    rng = random.Random(3000 + seed)
    points = month_points(rng, AS_OF, [rng.randint(15, 600) for _ in range(13)])
    shuffled = points[:]
    rng.shuffle(shuffled)
    assert percents(points, AS_OF) == percents(shuffled, AS_OF)


@pytest.mark.parametrize("seed", range(20))
def test_percent_matches_an_independent_fraction_calculation(seed):
    from fractions import Fraction

    rng = random.Random(4000 + seed)
    old = rng.randint(10, 5000)
    new = rng.randint(0, old * 50)
    built = build_series([pt("2025-10-13", old), pt("2026-10-13", new)], as_of=AS_OF)
    got = compute_change(built, 12, as_of=AS_OF).percent
    exact = Fraction(new - old, old) * 100
    scaled = exact * 100  # hundredths of a percent
    floor = scaled.numerator // scaled.denominator
    remainder = scaled - floor
    half_up_away = (
        (floor + 1)
        if (remainder >= Fraction(1, 2) if scaled >= 0 else remainder > Fraction(1, 2))
        else floor
    )
    assert got == Decimal(half_up_away) / 100


# ----------------------------------------------------------------- no floats anywhere


def test_all_numbers_are_decimals():
    report = full_report()
    for result in report.all_results():
        for value in (result.percent, result.absolute_change, result.months_actual):
            assert value is None or type(value) is Decimal
        for point in (result.from_point, result.to_point):
            assert point is None or type(point.count) is Decimal


def test_package_source_has_no_float_usage():
    root = Path(growth.__file__).parent
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Name) and node.id == "float"), path.name
            assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), path.name
