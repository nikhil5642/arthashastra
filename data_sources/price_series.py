"""PriceSeries — adjusted daily closes with date lookups, and what the tape says about risk.

Shared by the live valuation ratios (price at past fiscal year-ends) and
the backtester (forward returns, price as of a past date), so both read
prices the same way.

`annualised_volatility_pct` and `worst_fall_pct` measure how a price has
behaved. They are how price risk gets onto a snapshot: measured from the
company's own closes, never assumed from its size.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

# A lookup with no close within this many days (a listing that didn't
# exist yet, a long trading halt) has no usable price.
MAX_GAP_DAYS = 10
TRADING_DAYS_PER_YEAR = 252
# About six months of sessions: fewer say too little about how a price behaves.
MIN_CLOSES_FOR_RISK = 120


def annualised_volatility_pct(closes: Sequence[float]) -> float | None:
    """How much the price swings: the spread of daily returns, scaled to a year, in percent.

    30% is a calm large company; 70% moves about 4% on an ordinary day.
    None with fewer than `MIN_CLOSES_FOR_RISK` closes.
    """
    if len(closes) < MIN_CLOSES_FOR_RISK:
        return None
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(variance * TRADING_DAYS_PER_YEAR) * 100, 1)


def worst_fall_pct(closes: Sequence[float]) -> float | None:
    """The deepest fall from a high to a later low within `closes`, in percent.

    None with fewer than `MIN_CLOSES_FOR_RISK` closes.
    """
    if len(closes) < MIN_CLOSES_FOR_RISK:
        return None
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0:
            worst = max(worst, 1 - close / peak)
    return round(worst * 100, 1)


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
