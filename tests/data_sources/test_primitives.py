"""Primitive tests — date-matched lookups, trailing windows, and growth of a wiped-out profit."""

from __future__ import annotations

import pytest

from data_sources.fundamentals_source.primitives import (
    PROFIT_WIPED_OUT_CAGR,
    _screener_cagr,
    cagr_pct,
    change_by_date,
    consecutive_quarters,
    index_days_before,
    pct_change_by_date,
    trailing_twelve_months,
)

QUARTERS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]


def test_trailing_twelve_months_sums_the_four_newest_quarters():
    assert trailing_twelve_months([40.0, 30.0, 20.0, 10.0, 99.0], QUARTERS) == 100.0


def test_trailing_twelve_months_needs_four_consecutive_complete_quarters():
    # A dropped quarter would make the "twelve months" span fifteen.
    gappy = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-09-30"]
    assert trailing_twelve_months([40.0, 30.0, 20.0, 10.0], gappy) is None
    assert trailing_twelve_months([40.0, None, 20.0, 10.0], QUARTERS) is None  # a missing cell
    assert trailing_twelve_months([40.0, 30.0, 20.0], QUARTERS[:3]) is None  # only three quarters
    # Half-yearly reporters publish two periods a year — never four quarters.
    half_years = ["2026-06-30", "2025-12-31", "2025-06-30", "2024-12-31"]
    assert trailing_twelve_months([40.0, 30.0, 20.0, 10.0], half_years) is None


def test_consecutive_quarters_tolerates_month_end_and_53_week_calendars():
    assert consecutive_quarters(["2026-08-31", "2026-05-31", "2026-02-28", "2025-11-30"])
    assert consecutive_quarters(["2026-10-03", "2026-06-27", "2026-03-28", "2025-12-27"])  # 98-day quarter
    assert not consecutive_quarters(["2026-09-30", "bad date", "2026-03-31", "2025-12-31"])


def test_year_ago_lookups_go_by_date_not_position():
    assert index_days_before(QUARTERS, current_idx=0, days_ago=365, tolerance=45) == 4
    # With the year-ago quarter missing, the nearest entry is 9 months back — not a match.
    assert index_days_before(QUARTERS[:4], current_idx=0, days_ago=365, tolerance=45) is None
    assert index_days_before(QUARTERS, current_idx=9, days_ago=365, tolerance=45) is None


def test_change_by_date_returns_the_matched_pair():
    margins = [31.8, 34.0, 36.1, 31.6, 30.5]
    assert change_by_date(margins, QUARTERS) == (31.8, 30.5)
    assert change_by_date([31.8, 34.0, 36.1, 31.6, None], QUARTERS) is None  # year-ago cell missing
    assert change_by_date(margins[:4], QUARTERS[:4]) is None  # no year-ago quarter


def test_pct_change_needs_a_positive_base_and_both_values():
    assert pct_change_by_date([150.0, 0, 0, 0, 100.0], QUARTERS, current_idx=0, days_ago=365, tolerance=45) == 50.0
    assert pct_change_by_date([150.0, 0, 0, 0, -20.0], QUARTERS, current_idx=0, days_ago=365, tolerance=45) is None
    assert pct_change_by_date([None, 0, 0, 0, 100.0], QUARTERS, current_idx=0, days_ago=365, tolerance=45) is None


def test_cagr_of_a_profit_that_became_a_loss_is_known_bad_not_unknown():
    assert cagr_pct(100.0, 200.0, years=3) == pytest.approx(25.99)
    # Revenue rows: a non-positive endpoint has no growth rate.
    assert cagr_pct(100.0, -10.0, years=3) is None
    # Profit rows: the whole profit is gone — the worst outcome, reported as −100%.
    assert cagr_pct(100.0, -10.0, years=3, wiped_out_counts=True) == PROFIT_WIPED_OUT_CAGR
    assert cagr_pct(100.0, 0.0, years=3, wiped_out_counts=True) == PROFIT_WIPED_OUT_CAGR
    # A loss that became a profit has no base to grow from, either way.
    assert cagr_pct(-10.0, 100.0, years=3, wiped_out_counts=True) is None
    assert cagr_pct(float("nan"), 100.0, years=3) is None


def test_screener_cagr_passes_the_wiped_out_rule_through():
    profits = [50.0, 30.0, 10.0, -5.0]
    assert _screener_cagr(profits, years=3) is None
    assert _screener_cagr(profits, years=3, wiped_out_counts=True) == PROFIT_WIPED_OUT_CAGR
