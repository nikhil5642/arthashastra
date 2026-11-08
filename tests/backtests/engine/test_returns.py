"""Price series + forward return tests."""

from __future__ import annotations

from datetime import date

import pytest

from data_sources.price_series import PriceSeries
from backtests.engine.returns import forward_return


def _series() -> PriceSeries:
    return PriceSeries.from_rows(
        [
            ("2024-01-02", 100.0),
            ("2024-07-01", 110.0),
            ("2025-01-02", 120.0),
            ("bad-date", 999.0),
            ("2025-01-03", -5.0),  # non-positive closes are dropped
        ]
    )


def test_from_rows_sorts_and_cleans():
    series = _series()
    assert series.dates == [date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2)]
    assert series.first_date == date(2024, 1, 2)


def test_forward_return_over_a_year():
    assert forward_return(_series(), date(2023, 12, 31), 12) == pytest.approx(20.0)


def test_incomplete_or_pre_listing_windows_have_no_return():
    series = _series()
    assert forward_return(series, date(2025, 1, 1), 12) is None  # runs past the data
    assert forward_return(series, date(2020, 1, 1), 12) is None  # before the listing


def test_closes_through_only_sees_the_past():
    assert _series().closes_through(date(2024, 12, 31)) == [100.0, 110.0]
    assert PriceSeries().first_date is None


def test_close_on_or_before_is_what_was_known_that_evening():
    series = _series()
    assert series.close_on_or_before(date(2024, 7, 3)) == 110.0
    assert series.close_on_or_before(date(2024, 1, 1)) is None  # before the listing
    assert series.close_on_or_before(date(2024, 12, 1)) is None  # stale: > 10 days old
