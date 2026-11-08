"""PriceSeries — adjusted daily closes with date lookups.

Shared by the live valuation ratios (price at past fiscal year-ends) and
the backtester (forward returns, price as of a past date), so both read
prices the same way.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

# A lookup with no close within this many days (a listing that didn't
# exist yet, a long trading halt) has no usable price.
MAX_GAP_DAYS = 10


@dataclass(slots=True)
class PriceSeries:
    """Adjusted closes sorted by date (splits and dividends folded in)."""

    dates: list[date] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, float]]) -> "PriceSeries":
        by_day: dict[date, float] = {}
        for iso, close in rows:
            try:
                day = date.fromisoformat(iso[:10])
            except ValueError:
                continue
            if close > 0:
                by_day[day] = close
        days = sorted(by_day)
        return cls(dates=days, closes=[by_day[d] for d in days])

    @property
    def first_date(self) -> date | None:
        return self.dates[0] if self.dates else None

    def close_on_or_after(self, day: date) -> float | None:
        i = bisect_left(self.dates, day)
        if i == len(self.dates) or self.dates[i] - day > timedelta(days=MAX_GAP_DAYS):
            return None
        return self.closes[i]

    def close_on_or_before(self, day: date) -> float | None:
        """The last close known on the evening of `day`."""
        i = bisect_right(self.dates, day)
        if i == 0 or day - self.dates[i - 1] > timedelta(days=MAX_GAP_DAYS):
            return None
        return self.closes[i - 1]

    def closes_through(self, day: date) -> list[float]:
        """Every close on or before `day` — what was known that evening."""
        return self.closes[: bisect_right(self.dates, day)]
