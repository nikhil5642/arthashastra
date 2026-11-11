"""Valuation ratios — a company's price set against what it earns and owns.

Filled into an assembled snapshot from its own fields plus the listing's
daily closes, by the live pipeline AND the point-in-time backtest, so
both price a company the same way:

  - P/E             the source's published trailing P/E when there is one
                    (live), else price ÷ latest annual EPS (backtests)
  - earnings yield  100 / P/E
  - FCF yield       free cash flow ÷ market value of the equity — over the
                    trailing twelve months when the quarters are in hand,
                    else the latest fiscal year
  - price / book    price ÷ book value per share
  - P/E history     price at each past fiscal year-end ÷ that year's EPS

Today's price is always set against the freshest twelve months of cash
flow the source has: a share bought now is not paid for with last year's
cash. `earnings_window` and `cash_flow_window` record which twelve months
each ratio covers.

The equity's market value is shares × price, with shares derived as net
income ÷ EPS of the same fiscal year (which holds for every source and on
any past date). EPS is on today's share basis, like the split-adjusted
closes: screener and Yahoo restate it for later splits, and backtests
rebase SEC's first-filed EPS (see history.rebase_sec_eps).

Currencies: a ratio that divides a price by a statement amount needs both
in one currency. When they differ (an ADR priced in USD with CNY accounts,
a Hong Kong share of a mainland company, a London share quoted in pence
with USD accounts) the live path converts the market cap with a spot FX
rate, so FCF yield and P/B still exist; without a rate (backtests) they
are left out rather than mixed. P/E history needs an FX rate for every
past year-end and is same-currency only. A source-published P/E is kept
either way: the source already computes it consistently.

Quotes that don't reconcile: for a listing whose two currencies differ,
the price and the statements have to be shown to describe the same thing
before any ratio mixes them. Two tests, and failing either discards every
price-based ratio and marks the snapshot `quote_unreliable`:

  - A profitable company cannot trade for less than 1.5× one year's
    earnings. A quote that says so divides one instrument's price by
    another's earnings — an Argentine CEDEAR, a Singapore depositary
    receipt, a fractional Canadian receipt, whose "market cap" is only the
    receipt programme's.
  - The FX-converted market cap ÷ profit must land within 3× of the
    source's own P/E. When it doesn't, either the market cap is a receipt
    programme's or the statements carry the wrong currency label (Yahoo
    tags some Hong Kong listings USD while their figures are in yuan).

When the second test can't be run (no published P/E, or a loss), the FX
ratios are simply left out, as they are without an FX rate.
"""

from __future__ import annotations

from datetime import date

from data_sources.fundamentals_source.snapshot import BalanceSheetSnapshot, StatementWindow
from data_sources.fx_source import major_unit
from data_sources.price_series import PriceSeries

MIN_PLAUSIBLE_PE = 1.5
# FX-converted market cap ÷ profit vs the source's own P/E. Trailing vs
# fiscal-year profit and one-off items explain a gap of 2×; a wrong currency
# label is a gap of 5× and more (USD↔CNY 7, USD↔HKD 8, USD↔INR 85).
RECONCILE_TOLERANCE = 3.0


def apply_valuation(
    snap: BalanceSheetSnapshot,
    prices: PriceSeries,
    *,
    as_of: date | None = None,
    fx_to_reporting: float | None = None,
) -> None:
    """Fill the valuation fields in place.

    `as_of` (backtests) prices the snapshot on that date and only uses
    closes known by then; live callers leave it None and keep the
    snapshot's current price. `fx_to_reporting` is how many units of the
    reporting currency one unit of the trading currency's major unit buys;
    pass it for a listing whose two currencies differ.
    """
    if as_of is not None:
        snap.current_price = prices.close_on_or_before(as_of)

    _, divisor = major_unit(snap.currency)
    price = snap.current_price / divisor if snap.current_price else None
    same_currency = _same_currency(snap)
    latest_eps = snap.eps_history.get(snap.latest_annual_period)

    _set_price_to_earnings(snap, price, latest_eps, same_currency)
    if not price:
        return

    shares = _shares(snap.net_income, latest_eps)
    equity_value = _equity_value(snap, price, shares, same_currency, fx_to_reporting)
    if not same_currency:
        reconciles = _reconciles(snap, equity_value)
        if reconciles is False:
            _discard_price_ratios(snap)
            return
        if reconciles is None:
            equity_value = None  # unproven basis: no ratio may mix the two currencies

    _set_cash_flow_yield(snap, equity_value)
    _set_price_to_book(snap, price, shares, equity_value, same_currency)
    if same_currency:
        snap.pe_history = _pe_history(snap.eps_history, prices, divisor, before=as_of)


# ── The individual ratios ────────────────────────────────────────────────────


def _set_price_to_earnings(
    snap: BalanceSheetSnapshot, price: float | None, latest_eps: float | None, same_currency: bool
) -> None:
    """P/E and earnings yield, and the twelve months of earnings behind them."""
    if snap.pe_ratio is not None:
        snap.earnings_window = StatementWindow.TTM  # sources publish a trailing P/E
    elif same_currency and price and latest_eps and latest_eps > 0:
        snap.pe_ratio = round(price / latest_eps, 2)
        snap.earnings_window = StatementWindow.FISCAL_YEAR
    if snap.pe_ratio is not None and snap.pe_ratio > 0:
        snap.earnings_yield = round(100 / snap.pe_ratio, 2)


def _set_cash_flow_yield(snap: BalanceSheetSnapshot, equity_value: float | None) -> None:
    free_cash_flow, window = snap.trailing("free_cash_flow")
    if not equity_value or free_cash_flow is None:
        return
    snap.fcf_yield = round(free_cash_flow / equity_value * 100, 2)
    snap.cash_flow_window = window


def _set_price_to_book(
    snap: BalanceSheetSnapshot,
    price: float,
    shares: float | None,
    equity_value: float | None,
    same_currency: bool,
) -> None:
    equity = snap.shareholders_equity
    book_per_share = snap.book_value_per_share if same_currency else None
    if book_per_share is None and same_currency and shares is not None and equity:
        book_per_share = equity / shares
    if book_per_share is not None:
        if book_per_share > 0:
            snap.price_to_book = round(price / book_per_share, 2)
    elif equity_value and equity and equity > 0:
        snap.price_to_book = round(equity_value / equity, 2)


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


# ── Inputs: currencies, share count, the equity's market value ───────────────


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


def _equity_value(
    snap: BalanceSheetSnapshot,
    price: float,
    shares: float | None,
    same_currency: bool,
    fx_to_reporting: float | None,
) -> float | None:
    """What the market pays for the whole equity, in the reporting currency."""
    if same_currency:
        return shares * price if shares is not None else snap.market_cap
    if fx_to_reporting and snap.market_cap:
        return snap.market_cap * fx_to_reporting
    return None


# ── Cross-currency quotes: do price and statements describe the same thing? ──


def _reconciles(snap: BalanceSheetSnapshot, equity_value: float | None) -> bool | None:
    """True / False when a cross-currency quote can be checked, None when it can't.

    See the module docstring for the two tests.
    """
    source_pe = snap.pe_ratio
    if source_pe is not None and source_pe < MIN_PLAUSIBLE_PE:
        return False
    profit, _ = snap.trailing("net_income")
    if equity_value is None or source_pe is None or profit is None or profit <= 0:
        return None
    converted_pe = equity_value / profit
    return source_pe / RECONCILE_TOLERANCE <= converted_pe <= source_pe * RECONCILE_TOLERANCE


def _discard_price_ratios(snap: BalanceSheetSnapshot) -> None:
    """Nothing price-based survives an unreliable quote — wrong numbers are worse than none."""
    snap.quote_unreliable = True
    snap.pe_ratio = None
    snap.earnings_yield = None
    snap.earnings_window = None
    snap.fcf_yield = None
    snap.cash_flow_window = None
    snap.price_to_book = None
    snap.pe_history = []
