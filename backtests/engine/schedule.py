"""Rebalance schedules — month-end dates stepping back from an end date."""

from __future__ import annotations

import calendar
from datetime import date
from enum import StrEnum


class Step(StrEnum):
    YEAR = "year"
    QUARTER = "quarter"

    @property
    def months(self) -> int:
        return 12 if self is Step.YEAR else 3


def rebalance_dates(*, end: date, years: int, step: Step) -> list[date]:
    """Month-end dates every `step`, oldest first, ending at the last month-end ≤ `end`.

    Anchoring on a month-end that hasn't happened yet would score a date in
    the future, so the newest date is the last *completed* month-end.
    """
    anchor = last_month_end_on_or_before(end)
    count = years * 12 // step.months
    return [shift_months(anchor, -i * step.months) for i in range(count, -1, -1)]


def last_month_end_on_or_before(day: date) -> date:
    this_month_end = month_end(day)
    if this_month_end == day:
        return day
    return shift_months(this_month_end, -1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def shift_months(day: date, months: int) -> date:
    """Same day-of-month `months` later (clamped to month end); month-ends stay month-ends."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last_day = calendar.monthrange(year, month + 1)[1]
    is_month_end = day.day == calendar.monthrange(day.year, day.month)[1]
    return date(year, month + 1, last_day if is_month_end else min(day.day, last_day))
