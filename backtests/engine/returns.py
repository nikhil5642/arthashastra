"""Forward returns over a total-return price series."""

from __future__ import annotations

from datetime import date

from data_sources.price_series import PriceSeries
from backtests.engine.schedule import shift_months


def forward_return(prices: PriceSeries, start: date, months: int) -> float | None:
    """% change from the first close on/after `start` to the first on/after `start + months`.

    None when either end has no nearby close — including windows that run
    past the last available price, so incomplete periods never count.
    """
    entry = prices.close_on_or_after(start)
    exit_ = prices.close_on_or_after(shift_months(start, months))
    if entry is None or exit_ is None:
        return None
    return (exit_ / entry - 1) * 100
