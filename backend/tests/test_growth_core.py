"""Headcount growth: month arithmetic, series building and the single-window formula (#97)."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from apps.research.growth import (
    GrowthConfig,
    GrowthMethod,
    GrowthStatus,
    HeadcountPoint,
    build_series,
    compute_change,
    group_points,
    months_between,
    provider_result,
    quantize_percent,
    shift_months,
)

AS_OF = dt.date(2026, 10, 13)


def d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def pt(day: str, count: int, source: str = "provider:x", dept: str | None = None) -> HeadcountPoint:
    return HeadcountPoint(d(day), count, source, dept)


def series(*points: HeadcountPoint, as_of: dt.date = AS_OF):
    return build_series(points, as_of=as_of)


# ----------------------------------------------------------------- month arithmetic


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        ("2026-03-31", -1, "2026-02-28"),
        ("2024-03-31", -1, "2024-02-29"),  # leap year
        ("2024-02-29", -12, "2023-02-28"),
        ("2024-02-29", 12, "2025-02-28"),
        ("2024-02-29", 48, "2028-02-29"),
        ("2026-01-31", 1, "2026-02-28"),
        ("2026-01-15", -1, "2025-12-15"),  # across a year boundary
        ("2026-10-13", -12, "2025-10-13"),
        ("2026-10-13", -3, "2026-07-13"),
        ("2026-08-31", -6, "2026-02-28"),
        ("2026-05-31", -3, "2026-02-28"),
        ("2026-10-31", -1, "2026-09-30"),
        ("2026-10-13", 0, "2026-10-13"),
    ],
)
def test_shift_months_clamps_to_month_end(start, months, expected):
    assert shift_months(d(start), months) == d(expected)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2025-10-13", "2026-10-13", "12.00"),
        ("2026-01-31", "2026-02-28", "1.00"),
        ("2026-01-10", "2026-02-25", "1.54"),  # 15 of the 28 days of the next month
        ("2026-01-31", "2026-03-30", "1.97"),  # clamped anchor, 2026-02-28
        ("2026-04-13", "2026-04-13", "0.00"),
    ],
)
def test_months_between(start, end, expected):
    assert months_between(d(start), d(end)) == Decimal(expected)


# ----------------------------------------------------------------- quantisation


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.125", "0.13"),  # half-up, where half-even would give 0.12
        ("-0.125", "-0.13"),  # halves go away from zero
        ("0.124999", "0.12"),
        ("-11.931818", "-11.93"),
        ("2.675", "2.68"),
        ("0", "0.00"),
    ],
)
def test_quantize_is_half_up(value, expected):
    assert quantize_percent(Decimal(value)) == Decimal(expected)


# ----------------------------------------------------------------- series builder


def test_build_series_sorts_and_keeps_provenance():
    built = series(pt("2026-05-01", 120, "a"), pt("2025-10-13", 100, "b"), pt("2026-10-13", 130))
    assert [p.date.isoformat() for p in built.points] == ["2025-10-13", "2026-05-01", "2026-10-13"]
    assert [p.source for p in built.points] == ["b", "a", "provider:x"]
    assert built.department is None
    assert built.rejected == ()


def test_duplicate_same_day_prefers_trusted_source_prefix():
    built = series(
        pt("2026-10-13", 90, "provider:apollo:1"),
        pt("2026-10-13", 95, "company:site"),
        pt("2026-10-13", 99, "other:thing"),
        pt("2026-10-13", 97, "manual:analyst"),
    )
    assert [(p.count, p.source) for p in built.points] == [(97, "manual:analyst")]
    assert len(built.rejected) == 3
    assert all("duplicate of 2026-10-13" in r.reason for r in built.rejected)
    assert "manual:analyst" in built.rejected[0].reason


def test_duplicate_tie_goes_to_first_alphabetical_source_then_first_input():
    built = series(pt("2026-10-13", 90, "provider:b"), pt("2026-10-13", 91, "provider:a"))
    assert built.points[0].source == "provider:a"
    again = series(pt("2026-10-13", 90, "provider:a"), pt("2026-10-13", 91, "provider:a"))
    assert again.points[0].count == 90


def test_custom_source_priority():
    config = GrowthConfig(source_priority=("provider:",))
    built = build_series(
        [pt("2026-10-13", 1, "manual:x"), pt("2026-10-13", 2, "provider:y")],
        as_of=AS_OF,
        config=config,
    )
    assert built.points[0].source == "provider:y"


@pytest.mark.parametrize(
    ("point", "fragment"),
    [
        (pt("2026-10-14", 10), "after as_of"),
        (pt("2027-01-01", 10), "after as_of"),
        (pt("2026-01-01", -1), "negative headcount"),
        (pt("2026-01-01", 5_000_001), "implausible headcount"),
        (pt("2026-01-01", 10, ""), "no source"),
        (pt("2026-01-01", 10, "   "), "no source"),
        (HeadcountPoint(d("2026-01-01"), 10.5, "x"), "integer"),  # type: ignore[arg-type]
        (HeadcountPoint(d("2026-01-01"), Decimal(10), "x"), "integer"),  # type: ignore[arg-type]
        (HeadcountPoint(d("2026-01-01"), True, "x"), "integer"),
        (HeadcountPoint("2026-01-01", 10, "x"), "not a date"),  # type: ignore[arg-type]
    ],
)
def test_invalid_points_are_rejected_with_reason(point, fragment):
    built = series(point, pt("2026-02-01", 50))
    assert [p.count for p in built.points] == [50]
    assert len(built.rejected) == 1
    assert built.rejected[0].point is point
    assert fragment in built.rejected[0].reason


def test_as_of_day_itself_is_allowed_and_datetime_is_converted():
    stamp = dt.datetime(2026, 10, 13, 18, 30)
    built = series(HeadcountPoint(stamp, 40, "x"))
    assert built.points[0].date == AS_OF
    assert type(built.points[0].date) is dt.date


def test_zero_headcount_is_valid():
    assert series(pt("2026-01-01", 0)).points[0].count == 0


def test_build_series_rejects_points_of_another_department():
    with pytest.raises(ValueError, match="does not belong"):
        build_series([pt("2026-01-01", 5, dept="sales")], as_of=AS_OF)
    with pytest.raises(ValueError, match="does not belong"):
        build_series([pt("2026-01-01", 5)], as_of=AS_OF, department="sales")


def test_department_series_normalises_synonyms():
    built = build_series(
        [pt("2026-01-01", 5, dept="Business Development"), pt("2026-02-01", 6, dept="BD")],
        as_of=AS_OF,
        department="bd",
    )
    assert [p.department for p in built.points] == ["bd", "bd"]
    assert built.department == "bd"


def test_group_points_splits_by_department_and_rejects_unknown():
    grouped = group_points(
        [
            pt("2026-01-01", 100),
            pt("2026-01-01", 10, dept="Sales"),
            pt("2026-02-01", 11, dept="المبيعات"),
            pt("2026-01-01", 3, dept="Legal"),
            pt("2026-01-01", 4, dept="Marketing"),
        ],
        as_of=AS_OF,
    )
    assert set(grouped.series) == {None, "sales", "marketing"}
    assert len(grouped.series["sales"].points) == 2
    assert [r.reason for r in grouped.rejected] == ["unknown department 'Legal'"]


# ----------------------------------------------------------------- exact percentages


def two(base: int, latest: int, *, base_day: str = "2025-10-13", months: int = 12):
    return compute_change(series(pt(base_day, base), pt("2026-10-13", latest)), months, as_of=AS_OF)


@pytest.mark.parametrize(
    ("base", "latest", "percent", "absolute", "status"),
    [
        (100, 88, "-12.00", "-12", GrowthStatus.OK),
        (176, 155, "-11.93", "-21", GrowthStatus.OK),
        (100, 100, "0.00", "0", GrowthStatus.OK),
        (100, 150, "50.00", "50", GrowthStatus.OK),
        (800, 801, "0.13", "1", GrowthStatus.OK),  # 0.125 rounds half up
        (800, 799, "-0.13", "-1", GrowthStatus.OK),
        (3, 7, "133.33", "4", GrowthStatus.LOW_BASE),
        (1, 2, "100.00", "1", GrowthStatus.LOW_BASE),
        (9, 9, "0.00", "0", GrowthStatus.LOW_BASE),
        (10, 11, "10.00", "1", GrowthStatus.OK),  # 10 employees is not a low base
        (10000, 1009999, "9999.99", "999999", GrowthStatus.OK),
    ],
)
def test_percent_table(base, latest, percent, absolute, status):
    result = two(base, latest)
    assert result.percent == Decimal(percent)
    assert result.absolute_change == Decimal(absolute)
    assert result.status == status
    assert result.method == GrowthMethod.OBSERVED
    assert result.months_actual == Decimal("12.00")
    assert result.months_requested == 12
    assert result.from_point.date == d("2025-10-13")
    assert result.to_point.date == AS_OF
    assert result.from_point.source == "provider:x"
    assert result.is_available


def test_low_base_is_computed_with_a_warning_reason():
    result = two(1, 2)
    assert result.status == GrowthStatus.LOW_BASE
    assert "baseline of 1 employees is below 10" in result.reason
    assert two(100, 120).reason == ""


def test_low_base_threshold_is_configurable():
    config = GrowthConfig(low_base_threshold=1)
    built = series(pt("2025-10-13", 3), pt("2026-10-13", 6))
    assert compute_change(built, 12, as_of=AS_OF, config=config).status == GrowthStatus.OK


def test_zero_baseline_is_undefined_not_zero_or_error():
    result = two(0, 5)
    assert result.status == GrowthStatus.UNDEFINED_BASELINE
    assert result.percent is None
    assert result.absolute_change == Decimal(5)
    assert "is 0" in result.reason
    assert not result.is_available
    both_zero = two(0, 0)
    assert both_zero.status == GrowthStatus.UNDEFINED_BASELINE
    assert both_zero.percent is None


def test_out_of_range_is_flagged_not_clamped():
    result = two(10, 100_000)
    assert result.status == GrowthStatus.OUT_OF_RANGE
    assert result.percent is None
    assert result.absolute_change == Decimal(99_990)
    assert "999900.00%" in result.reason
    assert not result.is_available
    assert two(10000, 1010000).status == GrowthStatus.OUT_OF_RANGE  # 10000.00
    assert two(1, 200).status == GrowthStatus.OUT_OF_RANGE  # also a low base: range wins


# ----------------------------------------------------------------- windows and tolerance


@pytest.mark.parametrize(
    ("months", "offset", "target"),
    [(3, 21, "2026-07-13"), (6, 30, "2026-04-13"), (12, 45, "2025-10-13")],
)
def test_default_tolerance_edges(months, offset, target):
    day = d(target)
    for sign in (-1, 1):
        edge = day + dt.timedelta(days=sign * offset)
        result = compute_change(
            series(HeadcountPoint(edge, 100, "p"), pt("2026-10-13", 110)), months, as_of=AS_OF
        )
        assert result.status == GrowthStatus.OK
        assert result.method == GrowthMethod.OBSERVED
        assert result.from_point.date == edge
        beyond = day + dt.timedelta(days=sign * (offset + 1))
        miss = compute_change(
            series(HeadcountPoint(beyond, 100, "p"), pt("2026-10-13", 110)), months, as_of=AS_OF
        )
        assert miss.status == GrowthStatus.INSUFFICIENT_DATA
        assert miss.percent is None
        assert f"no headcount point within {offset} days of {target}" in miss.reason


def test_tolerance_is_configurable_per_call_and_in_config():
    built = series(pt("2026-03-01", 100), pt("2026-10-13", 110))  # 43 days from 2026-04-13
    assert compute_change(built, 6, as_of=AS_OF).status == GrowthStatus.INSUFFICIENT_DATA
    assert compute_change(built, 6, as_of=AS_OF, tolerance_days=43).percent == Decimal("10.00")
    wide = GrowthConfig(tolerance_days={6: 60})
    assert compute_change(built, 6, as_of=AS_OF, config=wide).percent == Decimal("10.00")
    # windows without a configured tolerance use the fallback
    odd = compute_change(
        series(pt("2026-09-01", 100), pt("2026-10-13", 110)), 1, as_of=AS_OF, config=wide
    )
    assert odd.status == GrowthStatus.OK


def test_closest_point_wins_and_ties_go_to_the_earlier_date():
    built = series(
        pt("2026-04-01", 100), pt("2026-04-20", 200), pt("2026-04-13", 150), pt("2026-10-13", 300)
    )
    assert compute_change(built, 6, as_of=AS_OF).from_point.count == Decimal(150)
    tie = series(pt("2026-04-10", 100), pt("2026-04-16", 200), pt("2026-10-13", 300))
    assert compute_change(tie, 6, as_of=AS_OF).from_point.date == d("2026-04-10")


def test_negative_tolerance_and_bad_months_are_programming_errors():
    built = series(pt("2025-10-13", 100), pt("2026-10-13", 110))
    with pytest.raises(ValueError, match="tolerance_days"):
        compute_change(built, 12, as_of=AS_OF, tolerance_days=-1)
    for bad in (0, -3, True):
        with pytest.raises(ValueError, match="positive integer"):
            compute_change(built, bad, as_of=AS_OF)


def test_leap_day_as_of_uses_clamped_month_arithmetic():
    as_of = d("2024-02-29")
    built = series(pt("2023-02-28", 100), pt("2024-02-29", 120), as_of=as_of)
    result = compute_change(built, 12, as_of=as_of)
    assert result.percent == Decimal("20.00")
    assert result.from_point.date == d("2023-02-28")
    assert result.months_actual == Decimal("12.03")  # 366 days: a day over the 12-month anchor


def test_end_of_month_as_of():
    as_of = d("2026-03-31")
    built = series(pt("2026-02-28", 100), pt("2026-03-31", 110), as_of=as_of)
    result = compute_change(built, 1, as_of=as_of)
    assert result.percent == Decimal("10.00")
    assert result.from_point.date == d("2026-02-28")


# ----------------------------------------------------------------- not enough data


def test_empty_series():
    result = compute_change(series(), 6, as_of=AS_OF)
    assert result.status == GrowthStatus.INSUFFICIENT_DATA
    assert result.percent is None
    assert result.absolute_change is None
    assert result.method is None
    assert result.reason == "no headcount points on or before 2026-10-13"


def test_points_after_as_of_are_ignored_even_in_a_prebuilt_series():
    later = series(pt("2025-04-13", 100), pt("2026-04-13", 110), as_of=d("2026-12-31"))
    result = compute_change(later, 6, as_of=d("2026-05-01"))
    assert result.status == GrowthStatus.INSUFFICIENT_DATA  # only 2026-04-13 is usable
    assert compute_change(later, 12, as_of=d("2026-04-13")).percent == Decimal("10.00")
    none_left = compute_change(series(pt("2026-12-01", 5), as_of=d("2026-12-31")), 3, as_of=AS_OF)
    assert none_left.reason == "no headcount points on or before 2026-10-13"


def test_single_point_is_not_growth():
    result = compute_change(series(pt("2026-10-13", 100)), 12, as_of=AS_OF)
    assert result.status == GrowthStatus.INSUFFICIENT_DATA
    assert result.reason == "no headcount point within 45 days of 2025-10-13"
    assert result.to_point.count == Decimal(100)


def test_stale_latest_point_is_unavailable():
    built = series(pt("2025-10-13", 100), pt("2026-08-01", 120))
    result = compute_change(built, 12, as_of=AS_OF)
    assert result.status == GrowthStatus.INSUFFICIENT_DATA
    assert result.reason == (
        "latest headcount point is 73 days old (2026-08-01); at most 45 days allowed"
    )
    relaxed = GrowthConfig(latest_max_age_days=90)
    assert compute_change(built, 12, as_of=AS_OF, config=relaxed).percent == Decimal("20.00")


def test_latest_must_be_after_the_window_start():
    built = series(pt("2026-08-01", 100), pt("2026-09-01", 110))
    result = compute_change(built, 1, as_of=AS_OF)
    assert result.status == GrowthStatus.INSUFFICIENT_DATA
    assert "is not after the window start 2026-09-13" in result.reason


def test_baseline_never_equals_the_latest_point():
    result = compute_change(series(pt("2026-07-13", 100)), 3, as_of=d("2026-07-13"))
    assert result.status == GrowthStatus.INSUFFICIENT_DATA
    only_latest_in_window = series(pt("2026-07-20", 100), pt("2026-10-13", 110))
    assert compute_change(only_latest_in_window, 3, as_of=AS_OF).percent == Decimal("10.00")


# ----------------------------------------------------------------- interpolation


def interpolation_series(after: str = "2026-06-13"):
    return series(
        pt("2026-02-13", 200, "provider:a"), pt(after, 240, "provider:b"), pt("2026-10-13", 250)
    )


def test_interpolates_across_exactly_the_max_gap():
    result = compute_change(interpolation_series(), 6, as_of=AS_OF)  # 120 days apart
    assert result.status == GrowthStatus.OK
    assert result.method == GrowthMethod.INTERPOLATED
    baseline = Decimal(200) + Decimal(40) * Decimal(59) / Decimal(120)
    assert result.from_point.interpolated
    assert result.from_point.date == d("2026-04-13")
    assert result.from_point.count == baseline
    assert result.from_point.source == "provider:a, provider:b"
    assert [p.count for p in result.from_point.basis] == [200, 240]
    assert result.percent == quantize_percent((Decimal(250) - baseline) / baseline * 100)
    assert result.percent == Decimal("13.81")


def test_no_interpolation_beyond_the_max_gap():
    result = compute_change(interpolation_series("2026-06-14"), 6, as_of=AS_OF)  # 121 days
    assert result.status == GrowthStatus.INSUFFICIENT_DATA
    assert result.percent is None
    assert result.reason == (
        "no headcount point within 30 days of 2026-04-13; the points around it are 121 days "
        "apart (at most 120 for interpolation)"
    )
    wide = GrowthConfig(max_gap_days=121)
    assert compute_change(
        interpolation_series("2026-06-14"), 6, as_of=AS_OF, config=wide
    ).method == (GrowthMethod.INTERPOLATED)


def test_no_interpolation_without_a_point_on_each_side():
    only_before = series(pt("2026-01-01", 200), pt("2026-10-13", 250))
    # the latest point is after the target, so a 6 month baseline needs a point before AND after:
    result = compute_change(only_before, 6, as_of=AS_OF)
    assert result.status == GrowthStatus.INSUFFICIENT_DATA  # 2026-01-01 .. 2026-10-13 too wide
    no_before = series(pt("2026-06-20", 200), pt("2026-10-13", 250))
    result = compute_change(no_before, 6, as_of=AS_OF)
    assert result.reason == "no headcount point within 30 days of 2026-04-13"


def test_interpolated_baseline_of_zero_is_undefined_and_low_base_flags():
    zero = series(pt("2026-03-01", 0), pt("2026-05-30", 0), pt("2026-10-13", 8))
    assert compute_change(zero, 6, as_of=AS_OF).status == GrowthStatus.UNDEFINED_BASELINE
    small = series(pt("2026-03-01", 4), pt("2026-05-30", 8), pt("2026-10-13", 12))
    result = compute_change(small, 6, as_of=AS_OF)
    assert result.status == GrowthStatus.LOW_BASE
    assert result.method == GrowthMethod.INTERPOLATED


def test_interpolated_baseline_at_the_latest_point_boundary():
    # the 'after' neighbour may be the latest point itself
    built = series(pt("2026-06-20", 100), pt("2026-10-13", 200))
    assert compute_change(built, 3, as_of=AS_OF).method == GrowthMethod.INTERPOLATED


# ----------------------------------------------------------------- provider percentages


def test_provider_result_rounds_and_records_method():
    result = provider_result(Decimal("-4.555"), 6, source="provider:apollo")
    assert result.percent == Decimal("-4.56")
    assert result.method == GrowthMethod.PROVIDER
    assert result.provider_source == "provider:apollo"
    assert result.absolute_change is None
    assert result.from_point is None
    assert result.status == GrowthStatus.OK


def test_provider_result_edge_cases():
    unknown = provider_result(None, 3, source="p")
    assert unknown.status == GrowthStatus.INSUFFICIENT_DATA
    assert unknown.percent is None
    assert provider_result(Decimal("NaN"), 3, source="p").status == GrowthStatus.INSUFFICIENT_DATA
    assert provider_result(Decimal("Infinity"), 3, source="p").percent is None
    big = provider_result(Decimal("10000"), 3, source="p")
    assert big.status == GrowthStatus.OUT_OF_RANGE
    assert big.percent is None
    assert provider_result(Decimal("9999.99"), 3, source="p").percent == Decimal("9999.99")
    with pytest.raises(TypeError, match="never float"):
        provider_result(12.5, 3, source="p")  # type: ignore[arg-type]


def test_result_is_immutable():
    result = two(100, 88)
    with pytest.raises(AttributeError):
        result.percent = Decimal(1)  # type: ignore[misc]
    assert replace(result, percent=Decimal(1)).percent == Decimal(1)
