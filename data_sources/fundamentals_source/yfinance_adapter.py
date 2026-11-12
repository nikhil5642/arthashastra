"""yfinance adapter — the global baseline for every listed equity.

Every security, on any exchange Yahoo covers (US, India, China, Hong
Kong, Japan, Europe, …), gets its snapshot from here. For Indian
listings screener.in adds depth and wins on the ratios it publishes
(see `_SCREENER_WINS` in source.py); yfinance still supplies the daily
price history, current / quick ratios, net debt / EBITDA, sector /
industry, currencies, ownership percentages, and the analyst bundle.

Three views of the same company come out of one fetch:

  - the latest full fiscal year  → the scored ratios (audited, comparable)
  - the four newest quarters     → trailing-twelve-month profit and cash
                                   flow, so valuation never prices today's
                                   share against statements a year old
  - the latest balance sheet     → year-on-year changes in debt,
    against the one a year back    receivables, inventory, goodwill and
                                   share count — what the annual ratios
                                   only show a year late

`.info` and `.fast_info` are rate-limited in 2026 so we stick to the
DataFrame accessors and only touch `.info` as a best-effort final step
for metadata.

Tests monkeypatch `_fetch_yfinance_sync` on this module directly.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Callable, TypeVar

from data_sources.fundamentals_source.primitives import (
    cagr_pct,
    change_by_date,
    consecutive_quarters,
    latest_yoy_growth,
    trailing_twelve_months,
)
from data_sources.fundamentals_source.snapshot import SnapshotFields
from data_sources.price_series import (
    TRADING_DAYS_PER_YEAR,
    annualised_volatility_pct,
    worst_fall_pct,
)

if TYPE_CHECKING:
    # Forward references — only the type checker sees these. No runtime
    # import cost because `from __future__ import annotations` makes every
    # type hint a string that's only resolved by mypy / pyright.
    from pandas import DataFrame, Series
    from yfinance import Ticker

log = logging.getLogger(__name__)

PriceRow = tuple[str, float]
SplitRow = tuple[str, float]  # (ISO date, ratio) — 4.0 for a 4-for-1 split

_T = TypeVar("_T")

VOLUME_WINDOW_SESSIONS = 63  # three months of trading days
WORST_FALL_WINDOW_YEARS = 3
MAX_TREND_YEARS = 10  # fiscal years kept in the multi-year trend series
# A split inside the comparison year makes two share counts incomparable.
SPLIT_LOOKBACK_DAYS = 400

# ── Statement rows, by Yahoo's labels (first present wins) ───────────────────

_REVENUE = ("Total Revenue", "Operating Revenue")
# Core operating profit. Yahoo's "EBIT" row is NOT a substitute: it adds
# interest back to pre-tax profit, so it carries other income and one-off
# gains — the very things interest coverage and ROCE must exclude.
_OPERATING_INCOME = ("Operating Income", "Total Operating Income As Reported")
_NET_INCOME = ("Net Income", "Net Income Common Stockholders")
# Net income with unusual items (investment gains, write-offs, special
# charges) taken out, after tax — computed by Yahoo's data provider.
_NORMALIZED_INCOME = ("Normalized Income",)
_OPERATING_CASH_FLOW = ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
_CAPEX = ("Capital Expenditure",)
_FREE_CASH_FLOW = ("Free Cash Flow",)
_STOCK_COMPENSATION = ("Stock Based Compensation",)
_EQUITY = ("Common Stock Equity", "Stockholders Equity", "Total Stockholder Equity")
_TOTAL_DEBT = ("Total Debt", "Long Term Debt")
_TOTAL_ASSETS = ("Total Assets",)
_CURRENT_LIABILITIES = ("Current Liabilities", "Total Current Liabilities")
_RECEIVABLES = ("Accounts Receivable", "Receivables")
_INVENTORY = ("Inventory",)
_GOODWILL_AND_INTANGIBLES = (
    "Goodwill And Other Intangible Assets",
    "Other Intangible Assets",
    "Goodwill",
)
SHORT_RECORD_GROWTH_YEARS = 2  # the fallback span when only three fiscal years exist

_SHARES_OUTSTANDING = ("Ordinary Shares Number", "Share Issued")


def _yf_symbol(raw: str) -> str:
    """Canonical tickers ARE Yahoo symbols ('MSFT', 'RELIANCE.NS', '0700.HK').

    No market is ever assumed: a bare symbol means the US listing, exactly
    as on Yahoo. (This used to append '.NS' to everything, which silently
    turned every non-Indian ticker into a 404.)
    """
    return raw.strip().upper()


# ── Public entry point ───────────────────────────────────────────────────────


async def _fetch_yfinance(
    ticker: str,
) -> tuple[SnapshotFields, list[PriceRow]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_yfinance_sync, ticker)


def _fetch_yfinance_sync(
    ticker: str,
) -> tuple[SnapshotFields, list[PriceRow]]:
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed")
        return SnapshotFields(), []

    symbol = _yf_symbol(ticker)

    try:
        t: "Ticker" = yf.Ticker(symbol)
    except Exception as e:
        log.warning("yfinance Ticker() failed for %s: %s", ticker, e)
        return SnapshotFields(), []

    # Ten years of closes: the last year feeds the 52-week range and the dip
    # detector; the rest prices each past fiscal year-end for P/E history.
    # Split-adjusted only (auto_adjust=False → "Close"): dividend adjustment
    # would shrink every past price, and with it every past P/E.
    hist: "DataFrame | None" = _safe(
        lambda: t.history(period="10y", auto_adjust=False),
        label=f"history {ticker}",
    )
    bs: "DataFrame | None" = _safe(lambda: t.balance_sheet, label=f"balance_sheet {ticker}")
    is_: "DataFrame | None" = _safe(lambda: t.income_stmt, label=f"income_stmt {ticker}")
    cf: "DataFrame | None" = _safe(lambda: t.cashflow, label=f"cashflow {ticker}")
    q_is: "DataFrame | None" = _safe(
        lambda: t.quarterly_income_stmt, label=f"quarterly_income_stmt {ticker}"
    )
    q_cf: "DataFrame | None" = _safe(
        lambda: t.quarterly_cashflow, label=f"quarterly_cashflow {ticker}"
    )
    q_bs: "DataFrame | None" = _safe(
        lambda: t.quarterly_balance_sheet, label=f"quarterly_balance_sheet {ticker}"
    )

    out: SnapshotFields = SnapshotFields()
    price_history: list[PriceRow] = []

    _fill_price_context(out, price_history, hist)
    _fill_annual_period(out, is_)
    _fill_eps_history(out, is_)
    _fill_balance_sheet_derived(out, bs)
    _fill_income_statement_derived(out, is_, bs)
    _fill_cash_flow_derived(out, cf, is_)
    _fill_working_capital_days(out, is_, bs)
    _fill_asset_composition(out, bs)
    _fill_annual_trends(out, is_, bs)
    _fill_quarterly_trend(out, q_is)
    _fill_trailing_twelve_months(out, q_is, q_cf)
    _fill_balance_sheet_changes(out, q_bs, bs, _splits(hist))
    _fill_info_metadata(out, t, ticker)

    return out, price_history


# ── Long history for backtests ───────────────────────────────────────────────


@dataclass(slots=True)
class YahooHistory:
    """Everything a backtest needs from Yahoo for one listing.

    Two price series, both on today's share basis: `prices` is adjusted for
    splits only (what valuation ratios divide by EPS), `total_return` also
    folds dividends back in (what an investor actually earned).
    """

    income: "DataFrame | None"
    balance: "DataFrame | None"
    cashflow: "DataFrame | None"
    prices: list[PriceRow] = field(default_factory=list)  # oldest first
    total_return: list[PriceRow] = field(default_factory=list)  # oldest first
    splits: list[SplitRow] = field(default_factory=list)  # oldest first
    metadata: SnapshotFields = field(default_factory=SnapshotFields)


async def fetch_history_bundle(ticker: str) -> YahooHistory:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_history_bundle_sync, ticker)


def _fetch_history_bundle_sync(ticker: str) -> YahooHistory:
    """Max price history + splits + annual statements + listing metadata."""
    empty = YahooHistory(income=None, balance=None, cashflow=None)
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed")
        return empty
    try:
        t: "Ticker" = yf.Ticker(_yf_symbol(ticker))
    except Exception as e:
        log.warning("yfinance Ticker() failed for %s: %s", ticker, e)
        return empty

    hist: "DataFrame | None" = _safe(
        lambda: t.history(period="max", auto_adjust=False), label=f"max history {ticker}"
    )
    metadata = SnapshotFields()
    _fill_info_metadata(metadata, t, ticker)
    return YahooHistory(
        income=_safe(lambda: t.income_stmt, label=f"income_stmt {ticker}"),
        balance=_safe(lambda: t.balance_sheet, label=f"balance_sheet {ticker}"),
        cashflow=_safe(lambda: t.cashflow, label=f"cashflow {ticker}"),
        prices=_column_rows(hist, "Close"),
        total_return=_column_rows(hist, "Adj Close"),
        splits=_splits(hist),
        metadata=metadata,
    )


# ── Price context ────────────────────────────────────────────────────────────


def _fill_price_context(
    out: SnapshotFields,
    price_history: list[PriceRow],
    hist: "DataFrame | None",
) -> None:
    if hist is None or hist.empty:
        return
    closes = hist["Close"].dropna()
    if len(closes) == 0:
        return
    last_year = hist.iloc[-TRADING_DAYS_PER_YEAR:]
    out["current_price"] = float(closes.iloc[-1])
    out["high_52w"] = float(last_year["High"].max())
    out["low_52w"] = float(last_year["Low"].min())
    price_history.extend(_column_rows(hist, "Close"))
    _fill_price_risk(out, [close for _, close in price_history], hist)


def _fill_price_risk(out: SnapshotFields, closes: list[float], hist: "DataFrame") -> None:
    """How the price has behaved and how much of it trades — the tape's own account of risk."""
    out["price_history_years"] = round(len(closes) / TRADING_DAYS_PER_YEAR, 1)
    volatility = annualised_volatility_pct(closes[-(TRADING_DAYS_PER_YEAR + 1):])
    if volatility is not None:
        out["volatility_1y_pct"] = volatility
    worst_fall = worst_fall_pct(closes[-WORST_FALL_WINDOW_YEARS * TRADING_DAYS_PER_YEAR:])
    if worst_fall is not None:
        out["worst_fall_3y_pct"] = worst_fall
    if "Volume" in hist.columns:
        recent = hist["Volume"].dropna().iloc[-VOLUME_WINDOW_SESSIONS:]
        if len(recent) and float(recent.mean()) > 0:
            out["avg_daily_volume"] = float(recent.mean())


def _column_rows(hist: "DataFrame | None", column: str) -> list[PriceRow]:
    """(ISO date, value) for every non-missing value of a history column."""
    if hist is None or column not in hist.columns:
        return []
    rows: list[PriceRow] = []
    for ts, value in hist[column].dropna().items():
        day = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        rows.append((day, float(value)))
    return rows


def _splits(hist: "DataFrame | None") -> list[SplitRow]:
    """Every stock split in the price history, oldest first."""
    return [(day, ratio) for day, ratio in _column_rows(hist, "Stock Splits") if ratio > 0]


# ── Latest fiscal year: the scored ratios ────────────────────────────────────


def _fill_annual_period(
    out: SnapshotFields, is_: "DataFrame | None"
) -> None:
    """Date of the newest annual income-statement column (iloc[0])."""
    if is_ is None:
        return
    try:
        if is_.empty:
            return
        newest = is_.columns[0]
    except (AttributeError, IndexError):
        return
    if hasattr(newest, "date"):
        out["latest_annual_period"] = newest.date().isoformat()


def _fill_eps_history(out: SnapshotFields, is_: "DataFrame | None") -> None:
    """{fiscal-year end (ISO): diluted EPS} from the annual income statement."""
    if is_ is None:
        return
    for name in ("Diluted EPS", "Basic EPS"):
        if name not in is_.index:
            continue
        history: dict[str, float] = {}
        for column, value in is_.loc[name].items():
            eps = _finite_or_none(value)
            if eps is not None and hasattr(column, "date"):
                history[column.date().isoformat()] = eps
        if history:
            out["eps_history"] = history
            return


def _fill_balance_sheet_derived(
    out: SnapshotFields, bs: "DataFrame | None"
) -> None:
    total_debt = _bs_first(bs, _TOTAL_DEBT)
    equity = _bs_first(bs, _EQUITY)
    current_assets = _bs_first(bs, ("Current Assets",))
    current_liabilities = _bs_first(bs, _CURRENT_LIABILITIES)
    inventory = _bs_first(bs, _INVENTORY)

    if equity is not None:
        out["shareholders_equity"] = equity
    if total_debt is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = round(total_debt / equity, 3)

    if (
        current_assets is not None
        and current_liabilities is not None
        and current_liabilities > 0
    ):
        out["current_ratio"] = round(current_assets / current_liabilities, 3)
        if inventory is not None:
            out["quick_ratio"] = round(
                (current_assets - inventory) / current_liabilities, 3
            )


def _fill_income_statement_derived(
    out: SnapshotFields,
    is_: "DataFrame | None",
    bs: "DataFrame | None",
) -> None:
    # Core operating EBIT — excludes other income and one-off gains, the
    # same definition the screener path uses for interest coverage.
    ebit = _bs_first(is_, _OPERATING_INCOME)
    ebitda = _bs_first(is_, ("EBITDA", "Normalized EBITDA"))
    interest = _bs_first(is_, ("Interest Expense",))
    revenue = _bs_first(is_, _REVENUE)
    gross_profit = _bs_first(is_, ("Gross Profit",))
    cogs = _bs_first(is_, ("Cost Of Revenue", "Reconciled Cost Of Revenue"))
    sga = _bs_first(
        is_,
        (
            "Selling General And Administration",
            "Selling General And Administrative",
        ),
    )
    net_income = _bs_first(is_, _NET_INCOME)
    normalized_income = _bs_first(is_, _NORMALIZED_INCOME)
    pretax = _bs_first(is_, ("Pretax Income",))
    tax_provision = _bs_first(is_, ("Tax Provision",))

    total_debt = _bs_first(bs, _TOTAL_DEBT)
    net_debt_raw = _bs_first(bs, ("Net Debt",))
    cash = _bs_first(
        bs,
        (
            "Cash And Cash Equivalents",
            "Cash Cash Equivalents And Short Term Investments",
        ),
    )
    equity = _bs_first(bs, _EQUITY)
    total_assets = _bs_first(bs, _TOTAL_ASSETS)
    current_liabilities = _bs_first(bs, _CURRENT_LIABILITIES)

    # A negative value is kept on purpose: a core operating loss is
    # known-bad coverage (scored 0), not missing data.
    if ebit is not None and interest is not None and interest != 0:
        out["interest_coverage"] = round(ebit / abs(interest), 2)

    # Prefer direct Net Debt row; else compute from total_debt − cash.
    computed_net_debt: float | None = None
    if net_debt_raw is not None:
        computed_net_debt = net_debt_raw
    elif total_debt is not None and cash is not None:
        computed_net_debt = total_debt - cash
    if computed_net_debt is not None and ebitda is not None and ebitda > 0:
        out["net_debt_to_ebitda"] = round(computed_net_debt / ebitda, 2)

    if net_income is not None:
        out["net_income"] = net_income
    core_ratio = _core_earnings_ratio(normalized_income, net_income)
    if core_ratio is not None:
        out["core_earnings_ratio_fy"] = core_ratio
    if net_income is not None and equity is not None and equity > 0:
        out["roe"] = round(net_income / equity * 100, 2)
    if net_income is not None and total_assets is not None and total_assets > 0:
        out["roa"] = round(net_income / total_assets * 100, 2)

    if (
        ebit is not None
        and total_assets is not None
        and current_liabilities is not None
    ):
        capital_employed = total_assets - current_liabilities
        if capital_employed > 0:
            out["roce"] = round(ebit / capital_employed * 100, 2)

    # Three years of growth where the record allows; two when it holds only
    # three fiscal years, so a short record is scored (and can be vetoed) on
    # what exists instead of dropping growth altogether.
    for years in (3, SHORT_RECORD_GROWTH_YEARS):
        rev_cagr = _compute_cagr(is_, _REVENUE, years=years)
        # A profit that became a loss is the worst growth outcome, not a gap
        # in the data — see primitives.PROFIT_WIPED_OUT_CAGR.
        pat_cagr = _compute_cagr(is_, _NET_INCOME, years=years, wiped_out_counts=True)
        if rev_cagr is None and pat_cagr is None:
            continue
        if rev_cagr is not None:
            out["rev_3y_cagr"] = rev_cagr
        if pat_cagr is not None:
            out["pat_3y_cagr"] = pat_cagr
        out["growth_years"] = years
        break

    if revenue is not None and revenue > 0:
        out["revenue"] = revenue
        if gross_profit is not None:
            out["gross_margin"] = round(gross_profit / revenue * 100, 2)
        if ebit is not None:
            out["operating_margin"] = round(ebit / revenue * 100, 2)
        if net_income is not None:
            out["net_margin"] = round(net_income / revenue * 100, 2)
        if cogs is not None:
            out["cogs_to_revenue"] = round(abs(cogs) / revenue * 100, 2)
        if sga is not None:
            out["sga_to_revenue"] = round(abs(sga) / revenue * 100, 2)

    if pretax is not None and pretax > 0 and tax_provision is not None:
        out["tax_rate"] = round(tax_provision / pretax * 100, 2)


def _core_earnings_ratio(
    normalized_income: float | None, net_income: float | None
) -> float | None:
    """Profit without unusual items ÷ reported profit, for a profitable period.

    Both numbers come from the same statement column(s), so the ratio is
    source-consistent whatever the merge later does with `net_income`.
    """
    if normalized_income is None or net_income is None or net_income <= 0:
        return None
    return round(normalized_income / net_income, 3)


def _fill_cash_flow_derived(
    out: SnapshotFields,
    cf: "DataFrame | None",
    is_: "DataFrame | None",
) -> None:
    ocf = _bs_first(cf, _OPERATING_CASH_FLOW)
    fcf = _bs_first(cf, _FREE_CASH_FLOW)
    capex = _bs_first(cf, _CAPEX)
    stock_compensation = _bs_first(cf, _STOCK_COMPENSATION)
    net_income = _bs_first(is_, _NET_INCOME)

    if ocf is not None:
        out["operating_cash_flow"] = ocf
    if capex is not None:
        out["capex"] = abs(capex)  # Yahoo reports the outflow as a negative
    if stock_compensation is not None:
        out["stock_based_compensation"] = stock_compensation
    if ocf is not None and net_income is not None and net_income > 0:
        out["ocf_to_pat"] = round(ocf / net_income, 3)

    # Prefer the Free Cash Flow row; otherwise compute from OCF − capex.
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf - abs(capex)
    if fcf is not None:
        out["free_cash_flow"] = fcf
    if fcf is not None and net_income is not None and net_income > 0:
        out["fcf_to_pat"] = round(fcf / net_income, 3)


def _fill_working_capital_days(
    out: SnapshotFields,
    is_: "DataFrame | None",
    bs: "DataFrame | None",
) -> None:
    revenue = _bs_first(is_, _REVENUE)
    cogs = _bs_first(is_, ("Cost Of Revenue", "Reconciled Cost Of Revenue"))
    inventory = _bs_first(bs, _INVENTORY)
    accounts_receivable = _bs_first(bs, _RECEIVABLES)
    accounts_payable = _bs_first(bs, ("Accounts Payable", "Payables"))

    if revenue is not None and revenue > 0 and accounts_receivable is not None:
        out["debtor_days"] = round(accounts_receivable / revenue * 365, 1)
    if cogs is not None and cogs != 0:
        cogs_abs = abs(cogs)
        if inventory is not None:
            out["inventory_days"] = round(inventory / cogs_abs * 365, 1)
        if accounts_payable is not None:
            out["payable_days"] = round(accounts_payable / cogs_abs * 365, 1)
    if all(
        out.get(k) is not None
        for k in ("debtor_days", "inventory_days", "payable_days")
    ):
        out["cash_conversion_cycle_days"] = round(
            out["debtor_days"] + out["inventory_days"] - out["payable_days"], 1
        )


def _fill_asset_composition(
    out: SnapshotFields, bs: "DataFrame | None"
) -> None:
    equity = _bs_first(bs, _EQUITY)
    tangible_book = _bs_first(bs, ("Tangible Book Value", "Net Tangible Assets"))
    goodwill_intangibles = _bs_first(bs, _GOODWILL_AND_INTANGIBLES)
    if tangible_book is not None and equity is not None and equity > 0:
        out["tangible_book_ratio"] = round(tangible_book / equity, 3)
    if goodwill_intangibles is not None and equity is not None and equity > 0:
        out["goodwill_to_equity"] = round(goodwill_intangibles / equity, 3)


# ── Fiscal-year history: is the business improving or degrading? ─────────────


def _fill_annual_trends(
    out: SnapshotFields, is_: "DataFrame | None", bs: "DataFrame | None"
) -> None:
    """Sales, profit, margin and ROCE by fiscal year, oldest → newest.

    Works on any yfinance-shaped annual frames: Yahoo's four years or SEC's
    eighteen (the live path and the backtests both call it), so a US filer
    gets a full business cycle of margins, not just the recent upswing.
    """
    income = _dated_rows(is_, anchor=_REVENUE)
    if income is None:
        return
    revenue = income.first(_REVENUE) or []
    profit = income.first(_NET_INCOME) or []
    operating = income.first(_OPERATING_INCOME) or []
    balance = _dated_rows(bs, anchor=_TOTAL_ASSETS)

    sales: list[float] = []
    profits: list[float] = []
    net_margins: list[float] = []
    operating_margins: list[float] = []
    roce: list[float] = []
    for i in reversed(range(min(len(income.dates), MAX_TREND_YEARS))):
        year_revenue = revenue[i]
        if year_revenue is None or year_revenue <= 0:
            continue
        sales.append(year_revenue)
        year_profit = _at(profit, i)
        if year_profit is not None:
            profits.append(year_profit)
            net_margins.append(round(year_profit / year_revenue * 100, 2))
        year_operating = _at(operating, i)
        if year_operating is not None:
            operating_margins.append(round(year_operating / year_revenue * 100, 2))
            year_roce = _roce_at(balance, income.dates[i], year_operating)
            if year_roce is not None:
                roce.append(year_roce)

    for key, series in (
        ("sales_annual_trend", sales),
        ("net_profit_annual_trend", profits),
        ("net_margin_annual_trend", net_margins),
        ("operating_margin_annual_trend", operating_margins),
        ("roce_annual_trend", roce),
    ):
        if series:
            out[key] = series  # type: ignore[literal-required]


def _roce_at(balance: "_DatedRows | None", fiscal_end: str, ebit: float) -> float | None:
    """EBIT ÷ (total assets − current liabilities) at one fiscal year-end."""
    if balance is None or fiscal_end not in balance.dates:
        return None
    i = balance.dates.index(fiscal_end)
    total_assets = _at(balance.first(_TOTAL_ASSETS), i)
    current_liabilities = _at(balance.first(_CURRENT_LIABILITIES), i)
    if total_assets is None or current_liabilities is None:
        return None
    capital_employed = total_assets - current_liabilities
    return round(ebit / capital_employed * 100, 2) if capital_employed > 0 else None


# ── The quarters since: trends and trailing twelve months ────────────────────


def _fill_quarterly_trend(
    out: SnapshotFields, q_is: "DataFrame | None"
) -> None:
    q_rev, q_pat, q_dates = _extract_quarterly_series(q_is)
    if q_dates:
        out["quarterly_dates"] = q_dates
        out["quarterly_operating_margin"] = _quarterly_operating_margin(q_is, q_dates)
    if q_rev:
        out["quarterly_revenue"] = q_rev
        growth = latest_yoy_growth(q_rev, q_dates)
        if growth is not None:
            out["latest_yoy_revenue_growth"] = growth
    if q_pat:
        out["quarterly_pat"] = q_pat
        growth = latest_yoy_growth(q_pat, q_dates)
        if growth is not None:
            out["latest_yoy_pat_growth"] = growth


def _quarterly_operating_margin(
    q_is: "DataFrame | None", quarter_ends: list[str]
) -> list[float | None]:
    """Operating profit ÷ revenue (%) for each of `quarter_ends`, None where unknown."""
    income = _dated_rows(q_is, anchor=_REVENUE)
    if income is None:
        return [None] * len(quarter_ends)
    revenue = income.first(_REVENUE) or []
    operating = income.first(_OPERATING_INCOME) or []
    margins: list[float | None] = []
    for quarter_end in quarter_ends:
        margin: float | None = None
        if quarter_end in income.dates:
            i = income.dates.index(quarter_end)
            quarter_revenue, quarter_operating = _at(revenue, i), _at(operating, i)
            if quarter_revenue and quarter_revenue > 0 and quarter_operating is not None:
                margin = round(quarter_operating / quarter_revenue * 100, 2)
        margins.append(margin)
    return margins


def _fill_trailing_twelve_months(
    out: SnapshotFields, q_is: "DataFrame | None", q_cf: "DataFrame | None"
) -> None:
    """Twelve-month sums over the four newest consecutive quarters.

    The income statement sets the window. The cash-flow statement only
    contributes when its newest quarter is the same one, so every `ttm_`
    figure describes the same twelve months.
    """
    income = _dated_rows(q_is, anchor=_REVENUE)
    if income is None or not consecutive_quarters(income.dates):
        return
    out["ttm_period_end"] = income.dates[0]
    for key, row_names in (("ttm_revenue", _REVENUE), ("ttm_net_income", _NET_INCOME)):
        total = income.trailing(row_names)
        if total is not None:
            out[key] = total  # type: ignore[literal-required]
    core_ratio = _core_earnings_ratio(
        income.trailing(_NORMALIZED_INCOME), income.trailing(_NET_INCOME)
    )
    if core_ratio is not None:
        out["core_earnings_ratio_ttm"] = core_ratio

    cash = _dated_rows(q_cf, anchor=_OPERATING_CASH_FLOW)
    if cash is None or cash.dates[0] != income.dates[0] or not consecutive_quarters(cash.dates):
        return
    operating_cash_flow = cash.trailing(_OPERATING_CASH_FLOW)
    capex = cash.trailing(_CAPEX)
    free_cash_flow = cash.trailing(_FREE_CASH_FLOW)
    if free_cash_flow is None and operating_cash_flow is not None and capex is not None:
        free_cash_flow = operating_cash_flow - abs(capex)
    stock_compensation = cash.trailing(_STOCK_COMPENSATION)

    if operating_cash_flow is not None:
        out["ttm_operating_cash_flow"] = operating_cash_flow
    if capex is not None:
        out["ttm_capex"] = abs(capex)
    if free_cash_flow is not None:
        out["ttm_free_cash_flow"] = free_cash_flow
    if stock_compensation is not None:
        out["ttm_stock_based_compensation"] = stock_compensation


# ── The latest balance sheet against the one a year earlier ──────────────────


def _fill_balance_sheet_changes(
    out: SnapshotFields,
    q_bs: "DataFrame | None",
    bs: "DataFrame | None",
    splits: list[SplitRow] | None,
) -> None:
    """Year-on-year changes in debt, receivables, inventory, goodwill and share count.

    Uses the quarterly (or half-yearly) balance sheets when one sits a year
    before the newest, else the two latest fiscal year-ends. Every pair is
    matched by date, and each change needs a positive year-ago base.

    `splits` is the listing's split history. The share-count change is only
    computed when that history is known and shows no split inside the
    year: a split changes the count without issuing or retiring a single
    share of ownership.
    """
    sheet = _year_comparable(q_bs) or _year_comparable(bs)
    if sheet is None:
        return
    out["balance_sheet_date"] = sheet.dates[0]

    debt = sheet.year_pair(_TOTAL_DEBT)
    equity = sheet.year_pair(_EQUITY)
    goodwill = sheet.year_pair(_GOODWILL_AND_INTANGIBLES)

    for key, pair in (
        ("debt_change_1y_pct", debt),
        ("receivables_change_1y_pct", sheet.year_pair(_RECEIVABLES)),
        ("inventory_change_1y_pct", sheet.year_pair(_INVENTORY)),
        ("goodwill_change_1y_pct", goodwill),
    ):
        change = _pct_change(pair)
        if change is not None:
            out[key] = change  # type: ignore[literal-required]

    if debt is not None and equity is not None:
        if equity[0] > 0:
            out["latest_debt_to_equity"] = round(debt[0] / equity[0], 3)
        if equity[1] > 0:
            out["debt_to_equity_1y_ago"] = round(debt[1] / equity[1], 3)
    if goodwill is not None and equity is not None and equity[0] > 0:
        out["latest_goodwill_to_equity"] = round(goodwill[0] / equity[0], 3)

    if splits is not None and not _split_in_year_to(splits, sheet.dates[0]):
        change = _pct_change(sheet.year_pair(_SHARES_OUTSTANDING))
        if change is not None:
            out["share_count_change_1y_pct"] = change


def _year_comparable(df: "DataFrame | None") -> "_DatedRows | None":
    """The balance sheets, if one of them sits about a year before the newest."""
    sheet = _dated_rows(df, anchor=_TOTAL_ASSETS)
    if sheet is None or change_by_date(sheet.first(_TOTAL_ASSETS) or [], sheet.dates) is None:
        return None
    return sheet


def _pct_change(pair: tuple[float, float] | None) -> float | None:
    """% change of (latest, year-ago); None without a positive year-ago base."""
    if pair is None or pair[1] <= 0:
        return None
    latest, prior = pair
    return round((latest - prior) / prior * 100, 1)


def _split_in_year_to(splits: list[SplitRow], balance_sheet_date: str) -> bool:
    """Was there a stock split in the year leading up to `balance_sheet_date`?"""
    try:
        sheet_day = date.fromisoformat(balance_sheet_date)
    except ValueError:
        return False
    window_start = (sheet_day - timedelta(days=SPLIT_LOOKBACK_DAYS)).isoformat()
    return any(window_start <= day <= balance_sheet_date for day, _ in splits)


# ── Listing metadata (`.info`, best-effort) ──────────────────────────────────


def _fill_info_metadata(
    out: SnapshotFields, t: "Ticker", ticker: str
) -> None:
    """`.info` is best-effort — rate-limited on busy days."""
    info = _safe(lambda: t.info or {}, label=f"info {ticker}") or {}
    if not info:
        return
    out.setdefault(
        "company_name", info.get("longName") or info.get("shortName") or ""
    )
    out.setdefault("sector", info.get("sector") or "")
    out.setdefault("industry", info.get("industry") or "")
    out.setdefault("country", info.get("country") or "")
    out.setdefault("website", info.get("website") or "")
    _fill_listing(out, info)
    _fill_ownership(out, info)
    _fill_valuation(out, info)
    fte = info.get("fullTimeEmployees")
    if fte is not None:
        try:
            out.setdefault("full_time_employees", int(fte))
        except (TypeError, ValueError):
            pass
    summary = info.get("longBusinessSummary") or info.get("description") or ""
    if summary:
        out.setdefault("business_summary", str(summary)[:3000])
    # Analyst bundle — consensus targets and recommendation that
    # Bloomberg / Reuters terminals show for institutional users.
    for src_key, dst_key in (
        ("targetMeanPrice", "analyst_target_mean"),
        ("targetHighPrice", "analyst_target_high"),
        ("targetLowPrice", "analyst_target_low"),
        ("recommendationMean", "analyst_recommendation_mean"),
    ):
        v = info.get(src_key)
        if v is not None:
            try:
                out.setdefault(dst_key, float(v))
            except (TypeError, ValueError):
                pass
    n_analysts = info.get("numberOfAnalystOpinions")
    if n_analysts is not None:
        try:
            out.setdefault("analyst_opinion_count", int(n_analysts))
        except (TypeError, ValueError):
            pass


def _fill_listing(out: SnapshotFields, info: dict[str, object]) -> None:
    """Exchange, trading currency, reporting currency, market cap."""
    exchange = info.get("fullExchangeName") or info.get("exchange")
    if isinstance(exchange, str):
        out["exchange"] = exchange
    currency = info.get("currency")
    if isinstance(currency, str):
        out["currency"] = currency  # case matters: "GBp" is pence, not pounds
    financial_currency = info.get("financialCurrency") or currency
    if isinstance(financial_currency, str):
        out["financial_currency"] = financial_currency
    market_cap = _info_float(info, "marketCap")
    if market_cap is not None and market_cap > 0:
        out["market_cap"] = market_cap


def _fill_ownership(out: SnapshotFields, info: dict[str, object]) -> None:
    """Insider + institutional percentages (Yahoo publishes fractions)."""
    insiders = _info_float(info, "heldPercentInsiders")
    if insiders is not None:
        out["insider_holding"] = round(insiders * 100, 2)
    institutions = _info_float(info, "heldPercentInstitutions")
    if institutions is not None:
        out["institutional_holding"] = round(institutions * 100, 2)


def _fill_valuation(out: SnapshotFields, info: dict[str, object]) -> None:
    """P/E, dividend yield, and book value — only where currencies agree.

    Yahoo converts trailing EPS into the trading currency, so its P/E holds
    for every listing. Per-share book value stays in the *reporting*
    currency, and the trailing dividend yield divides a dividend by a price
    in another unit whenever the two differ: Lloyds, quoted in pence, shows
    0.04% instead of about 4%; Alibaba's ADR (USD price, CNY dividend) 6.7%
    instead of about 1%. For those listings both are left out rather than
    shown wrong.
    """
    pe = _info_float(info, "trailingPE")
    if pe is not None and pe > 0:
        out["pe_ratio"] = round(pe, 2)
    currency = info.get("currency")
    if currency != (info.get("financialCurrency") or currency):
        return
    dividend_yield = _info_float(info, "trailingAnnualDividendYield")
    if dividend_yield is not None:
        out["dividend_yield"] = round(dividend_yield * 100, 2)
    book_value = _info_float(info, "bookValue")
    if book_value is not None:
        out["book_value_per_share"] = book_value


def _info_float(info: dict[str, object], key: str) -> float | None:
    value = info.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


# ── DataFrame helpers ────────────────────────────────────────────────────────


@dataclass(slots=True)
class _DatedRows:
    """A statement as plain lists: its reporting dates, and each row aligned to them.

    `dates` are ISO strings, newest first, limited to real reporting
    periods (columns where the statement's anchor row has a value — Yahoo
    often carries an older column that is entirely empty).
    """

    dates: list[str]
    rows: dict[str, list[float | None]]

    def first(self, candidates: tuple[str, ...]) -> list[float | None] | None:
        """The first of `candidates` this statement has."""
        for name in candidates:
            if name in self.rows:
                return self.rows[name]
        return None

    def trailing(self, candidates: tuple[str, ...]) -> float | None:
        """Twelve-month sum of the first candidate row with four full quarters."""
        for name in candidates:
            if name in self.rows:
                total = trailing_twelve_months(self.rows[name], self.dates)
                if total is not None:
                    return total
        return None

    def year_pair(self, candidates: tuple[str, ...]) -> tuple[float, float] | None:
        """(latest, about a year earlier) for the first candidate row with both."""
        for name in candidates:
            if name in self.rows:
                pair = change_by_date(self.rows[name], self.dates)
                if pair is not None:
                    return pair
        return None


def _dated_rows(df: "DataFrame | None", *, anchor: tuple[str, ...]) -> _DatedRows | None:
    """A statement frame as `_DatedRows`, or None when it has no usable period."""
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except Exception:
        return None
    anchor_row = _first_row(df, anchor)
    if anchor_row is None:
        return None
    columns = [
        column
        for column in df.columns
        if hasattr(column, "date") and _finite_or_none(anchor_row.get(column)) is not None
    ]
    if not columns:
        return None
    columns.sort(reverse=True)
    return _DatedRows(
        dates=[column.date().isoformat() for column in columns],
        rows={
            str(name): [_finite_or_none(df.at[name, column]) for column in columns]
            for name in df.index
        },
    )


def _at(row: list[float | None] | None, i: int) -> float | None:
    return row[i] if row is not None and i < len(row) else None


def _extract_quarterly_series(
    q_is: "DataFrame | None",
) -> tuple[list[float], list[float], list[str]]:
    """Return aligned (revenue, net_income, date_iso) lists, newest-first.

    A quarter is kept only when every available row has a finite value
    for it, so index `i` means the same quarter in all three lists.
    yfinance leaves NaN holes in different quarters for different rows,
    and dropping them independently used to shift PAT against its dates.
    """
    if q_is is None:
        return [], [], []
    try:
        if q_is.empty:
            return [], [], []
    except Exception:
        return [], [], []

    rev_row = _first_row(q_is, _REVENUE)
    pat_row = _first_row(q_is, _NET_INCOME)
    if rev_row is None:
        return [], [], []

    revenue: list[float] = []
    pat: list[float] = []
    dates: list[str] = []
    for col in q_is.columns:
        rev = _finite_or_none(rev_row.get(col))
        profit = _finite_or_none(pat_row.get(col)) if pat_row is not None else None
        if rev is None or (pat_row is not None and profit is None):
            continue
        revenue.append(rev)
        if profit is not None:
            pat.append(profit)
        dates.append(col.date().isoformat() if hasattr(col, "date") else str(col)[:10])
    return revenue, pat, dates


def _first_row(df: "DataFrame", candidates: tuple[str, ...]) -> "Series | None":
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _consecutive_years(columns: object, years: int) -> bool:
    """True unless dated columns 0 and `years` are clearly not `years` apart.

    Guards CAGRs on deep histories (SEC) with a missing fiscal year, where
    "3 columns back" would silently be 4 years.
    """
    try:
        newest, oldest = columns[0], columns[years]  # type: ignore[index]
        span_years = (newest - oldest).days / 365.25
    except (TypeError, AttributeError, IndexError):
        return True  # undated columns: trust the positional window
    return abs(span_years - years) <= 0.25


def _safe(fn: Callable[[], _T], *, label: str) -> _T | None:
    """Call `fn()`, log any exception, return None on failure.

    Used to wrap yfinance accessors whose failure modes include network
    errors, rate-limit responses, and mid-request resets — any of which
    should degrade gracefully rather than crash the refresh.
    """
    try:
        return fn()
    except Exception as e:
        log.debug("yfinance %s raised %s", label, e)
        return None


def _bs_first(
    df: "DataFrame | None", candidates: tuple[str, ...]
) -> float | None:
    """The newest column's value for the first of `candidates` that has one."""
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except Exception:
        return None
    for name in candidates:
        try:
            if name in df.index:
                value = _finite_or_none(df.loc[name].iloc[0])
                if value is not None:
                    return value
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None


def _compute_cagr(
    df: "DataFrame | None",
    candidates: tuple[str, ...],
    *,
    years: int,
    wiped_out_counts: bool = False,
) -> float | None:
    """CAGR from a yfinance DataFrame's row. iloc[0] is newest.

    yfinance often leaves the oldest annual column NaN for Indian and other
    non-US filers; a NaN endpoint gives None (it would otherwise slip past
    every `<=` check and score growth 0/100 while bypassing the growth veto).
    """
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except Exception:
        return None
    for name in candidates:
        if name not in df.index:
            continue
        try:
            series = df.loc[name]
            if len(series) < years + 1 or not _consecutive_years(series.index, years):
                continue
            growth = cagr_pct(
                float(series.iloc[years]),
                float(series.iloc[0]),
                years=years,
                wiped_out_counts=wiped_out_counts,
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if growth is not None:
            return growth
    return None
