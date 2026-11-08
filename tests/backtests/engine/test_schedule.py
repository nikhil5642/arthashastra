"""Rebalance schedule tests — calendar arithmetic only."""

from __future__ import annotations

from datetime import date

from backtests.engine.schedule import (
    Step,
    last_month_end_on_or_before,
    month_end,
    rebalance_dates,
    shift_months,
)


def test_thirty_years_yearly_is_thirty_one_dates_ending_at_last_month_end():
    dates = rebalance_dates(end=date(2026, 11, 8), years=30, step=Step.YEAR)
    assert len(dates) == 31
    assert dates[-1] == date(2026, 10, 31)  # never a month-end in the future
    assert dates[0] == date(1996, 10, 31)


def test_quarterly_steps_three_months():
    dates = rebalance_dates(end=date(2026, 9, 30), years=1, step=Step.QUARTER)
    assert dates == [
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
        date(2026, 6, 30),
        date(2026, 9, 30),
    ]


def test_month_end_arithmetic_handles_leap_years():
    assert month_end(date(2024, 2, 10)) == date(2024, 2, 29)
    assert shift_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert shift_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert shift_months(date(2024, 1, 15), 1) == date(2024, 2, 15)
    assert last_month_end_on_or_before(date(2026, 3, 31)) == date(2026, 3, 31)


def test_step_months():
    assert Step.YEAR.months == 12
    assert Step.QUARTER.months == 3
