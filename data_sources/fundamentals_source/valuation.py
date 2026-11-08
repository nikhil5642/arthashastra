"""Valuation ratios — a company's price set against what it earns and owns.

Filled into an assembled snapshot from its own fields plus the listing's
daily closes, by the live pipeline AND the point-in-time backtest, so
both price a company the same way:

  - P/E             the source's published P/E when there is one (live),
                    else price ÷ latest annual EPS (backtests)
  - earnings yield  100 / P/E
  - FCF yield       free cash flow per share ÷ price
  - price / book    price ÷ book value per share
  - P/E history     price at each past fiscal year-end ÷ that year's EPS

Shares are derived as net income ÷ EPS of the same fiscal year (the
snapshot's `latest_annual_period`), which holds for every source. EPS is
on today's share basis, like the split-adjusted closes: screener and
Yahoo restate it for later splits, and backtests rebase SEC's first-filed
EPS (see history.rebase_sec_eps). Ratios that divide a price by a statement amount
need both in one currency; when they differ (an ADR priced in USD with
CNY accounts, a London share quoted in pence with USD accounts) they are
left out rather than mixed. A source-published P/E is kept either way:
the source already computes it consistently.
"""

from __future__ import annotations

from datetime import date

from data_sources.fundamentals_source.snapshot import BalanceSheetSnapshot
from data_sources.fx_source import major_unit
from data_sources.price_series import PriceSeries


def apply_valuation(
    snap: BalanceSheetSnapshot, prices: PriceSeries, *, as_of: date | None = None
) -> None:
    """Fill the valuation fields in place.

    `as_of` (backtests) prices the snapshot on that date and only uses
    closes known by then; live callers leave it None and keep the
    snapshot's current price.
    """
    if as_of is not None:
        snap.current_price = prices.close_on_or_before(as_of)

    _, divisor = major_unit(snap.currency)
    price = snap.current_price / divisor if snap.current_price else None
    same_currency = _same_currency(snap)
    latest_eps = snap.eps_history.get(snap.latest_annual_period)

    if snap.pe_ratio is None and same_currency and price and latest_eps and latest_eps > 0:
        snap.pe_ratio = round(price / latest_eps, 2)
    if snap.pe_ratio is not None and snap.pe_ratio > 0:
        snap.earnings_yield = round(100 / snap.pe_ratio, 2)

    if not same_currency or not price:
        return

    shares = _shares(snap.net_income, latest_eps)
    if shares is not None and snap.free_cash_flow is not None:
        snap.fcf_yield = round(snap.free_cash_flow / shares / price * 100, 2)

    book_per_share = snap.book_value_per_share
    if book_per_share is None and shares is not None and snap.shareholders_equity:
        book_per_share = snap.shareholders_equity / shares
    if book_per_share is not None and book_per_share > 0:
        snap.price_to_book = round(price / book_per_share, 2)

    snap.pe_history = _pe_history(snap.eps_history, prices, divisor, before=as_of)


def _same_currency(snap: BalanceSheetSnapshot) -> bool:
    if not snap.currency or not snap.financial_currency:
        return False
    return major_unit(snap.currency)[0] == snap.financial_currency.upper()


def _shares(net_income: float | None, eps: float | None) -> float | None:
    """Share count implied by the same fiscal year's profit and EPS."""
    if net_income is None or not eps:
        return None
    shares = net_income / eps
    return shares if shares > 0 else None


def _pe_history(
    eps_history: dict[str, float],
    prices: PriceSeries,
    divisor: float,
    *,
    before: date | None,
) -> list[float]:
    """P/E at each past fiscal year-end with positive EPS, oldest first.

    In a backtest `eps_history` only holds fiscal years already public on
    the as-of date (≥ 90 days old), so the year-end close is always known.
    """
    history: list[float] = []
    for fiscal_end, eps in sorted(eps_history.items()):
        day = date.fromisoformat(fiscal_end)
        if eps <= 0 or (before is not None and day > before):
            continue
        close = prices.close_on_or_after(day)
        if close is not None:
            history.append(round(close / divisor / eps, 2))
    return history
