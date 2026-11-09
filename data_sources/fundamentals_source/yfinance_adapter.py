"""yfinance adapter — the global baseline for every listed equity.

Every security, on any exchange Yahoo covers (US, India, China, Hong
Kong, Japan, Europe, …), gets its snapshot from here. For Indian
listings screener.in adds depth and wins on the ratios it publishes
(see `_SCREENER_WINS` in source.py); yfinance still supplies the daily
price history, current / quick ratios, net debt / EBITDA, sector /
industry, currencies, ownership percentages, and the analyst bundle.

`.info` and `.fast_info` are rate-limited in 2026 so we stick to the
DataFrame accessors (`.history`, `.balance_sheet`, `.income_stmt`,
`.cashflow`, `.quarterly_income_stmt`) and only touch `.info` as a
best-effort final step for metadata.

Tests monkeypatch `_fetch_yfinance_sync` on this module directly.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, TypeVar

from data_sources.fundamentals_source.primitives import latest_yoy_growth
from data_sources.fundamentals_source.snapshot import SnapshotFields

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

TRADING_DAYS_PER_YEAR = 252

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
    bs: "DataFrame | None" = _safe(
        lambda: t.balance_sheet, label=f"balance_sheet {ticker}"
    )
    is_: "DataFrame | None" = _safe(
        lambda: t.income_stmt, label=f"income_stmt {ticker}"
    )
    cf: "DataFrame | None" = _safe(
        lambda: t.cashflow, label=f"cashflow {ticker}"
    )
    q_is: "DataFrame | None" = _safe(
        lambda: t.quarterly_income_stmt, label=f"quarterly_income_stmt {ticker}"
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
    _fill_quarterly_trend(out, q_is)
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
        splits=[(day, ratio) for day, ratio in _column_rows(hist, "Stock Splits") if ratio > 0],
        metadata=metadata,
    )


# ── Per-section fillers (keep _fetch_yfinance_sync readable) ─────────────────


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


def _column_rows(hist: "DataFrame | None", column: str) -> list[PriceRow]:
    """(ISO date, value) for every non-missing value of a history column."""
    if hist is None or column not in hist.columns:
        return []
    rows: list[PriceRow] = []
    for ts, value in hist[column].dropna().items():
        day = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        rows.append((day, float(value)))
    return rows


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
    total_debt = _bs_first(bs, ("Total Debt", "Long Term Debt"))
    equity = _bs_first(
        bs, ("Common Stock Equity", "Stockholders Equity", "Total Stockholder Equity")
    )
    current_assets = _bs_first(bs, ("Current Assets",))
    current_liabilities = _bs_first(
        bs, ("Current Liabilities", "Total Current Liabilities")
    )
    inventory = _bs_first(bs, ("Inventory",))

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
    ebit = _bs_first(is_, ("Operating Income", "EBIT"))
    ebitda = _bs_first(is_, ("EBITDA", "Normalized EBITDA"))
    interest = _bs_first(is_, ("Interest Expense",))
    revenue = _bs_first(is_, ("Total Revenue", "Operating Revenue"))
    gross_profit = _bs_first(is_, ("Gross Profit",))
    cogs = _bs_first(is_, ("Cost Of Revenue", "Reconciled Cost Of Revenue"))
    sga = _bs_first(
        is_,
        (
            "Selling General And Administration",
            "Selling General And Administrative",
        ),
    )
    net_income = _bs_first(is_, ("Net Income", "Net Income Common Stockholders"))
    pretax = _bs_first(is_, ("Pretax Income",))
    tax_provision = _bs_first(is_, ("Tax Provision",))

    total_debt = _bs_first(bs, ("Total Debt", "Long Term Debt"))
    net_debt_raw = _bs_first(bs, ("Net Debt",))
    cash = _bs_first(
        bs,
        (
            "Cash And Cash Equivalents",
            "Cash Cash Equivalents And Short Term Investments",
        ),
    )
    equity = _bs_first(
        bs, ("Common Stock Equity", "Stockholders Equity", "Total Stockholder Equity")
    )
    total_assets = _bs_first(bs, ("Total Assets",))
    current_liabilities = _bs_first(
        bs, ("Current Liabilities", "Total Current Liabilities")
    )

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

    rev_cagr = _compute_cagr(is_, ("Total Revenue", "Operating Revenue"), years=3)
    if rev_cagr is not None:
        out["rev_3y_cagr"] = rev_cagr
    pat_cagr = _compute_cagr(
        is_, ("Net Income", "Net Income Common Stockholders"), years=3
    )
    if pat_cagr is not None:
        out["pat_3y_cagr"] = pat_cagr

    if revenue is not None and revenue > 0:
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


def _fill_cash_flow_derived(
    out: SnapshotFields,
    cf: "DataFrame | None",
    is_: "DataFrame | None",
) -> None:
    ocf = _bs_first(
        cf,
        (
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
        ),
    )
    fcf = _bs_first(cf, ("Free Cash Flow",))
    capex = _bs_first(cf, ("Capital Expenditure",))
    net_income = _bs_first(is_, ("Net Income", "Net Income Common Stockholders"))

    if ocf is not None and net_income is not None and net_income > 0:
        out["ocf_to_pat"] = round(ocf / net_income, 3)

    # Prefer the Free Cash Flow row; otherwise compute from OCF + capex
    # (capex is a negative number in yfinance, so adding it subtracts).
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf + capex
    if fcf is not None:
        out["free_cash_flow"] = fcf
    if fcf is not None and net_income is not None and net_income > 0:
        out["fcf_to_pat"] = round(fcf / net_income, 3)


def _fill_working_capital_days(
    out: SnapshotFields,
    is_: "DataFrame | None",
    bs: "DataFrame | None",
) -> None:
    revenue = _bs_first(is_, ("Total Revenue", "Operating Revenue"))
    cogs = _bs_first(is_, ("Cost Of Revenue", "Reconciled Cost Of Revenue"))
    inventory = _bs_first(bs, ("Inventory",))
    accounts_receivable = _bs_first(bs, ("Accounts Receivable", "Receivables"))
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
    equity = _bs_first(
        bs, ("Common Stock Equity", "Stockholders Equity", "Total Stockholder Equity")
    )
    tangible_book = _bs_first(bs, ("Tangible Book Value", "Net Tangible Assets"))
    goodwill_intangibles = _bs_first(
        bs,
        (
            "Goodwill And Other Intangible Assets",
            "Other Intangible Assets",
            "Goodwill",
        ),
    )
    if tangible_book is not None and equity is not None and equity > 0:
        out["tangible_book_ratio"] = round(tangible_book / equity, 3)
    if goodwill_intangibles is not None and equity is not None and equity > 0:
        out["goodwill_to_equity"] = round(goodwill_intangibles / equity, 3)


def _fill_quarterly_trend(
    out: SnapshotFields, q_is: "DataFrame | None"
) -> None:
    q_rev, q_pat, q_dates = _extract_quarterly_series(q_is)
    if q_dates:
        out["quarterly_dates"] = q_dates
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

    rev_row = _first_row(q_is, ("Total Revenue", "Operating Revenue"))
    pat_row = _first_row(q_is, ("Net Income", "Net Income Common Stockholders"))
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
    """Return the first populated value across `candidates` row names."""
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
                val = df.loc[name].iloc[0]
                if val is None:
                    continue
                v = float(val)
                if v != v:  # NaN guard
                    continue
                return v
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None


def _compute_cagr(
    df: "DataFrame | None",
    candidates: tuple[str, ...],
    *,
    years: int,
) -> float | None:
    """CAGR from a yfinance DataFrame's row. iloc[0] is newest."""
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
            current = float(series.iloc[0])
            past = float(series.iloc[years])
            # yfinance often leaves the oldest annual column NaN for Indian
            # and other non-US filers. NaN slips through `<= 0` checks and
            # would score growth 0/100 while bypassing the growth veto.
            if not (math.isfinite(current) and math.isfinite(past)):
                continue
            if past <= 0 or current <= 0:
                continue
            return round((((current / past) ** (1 / years)) - 1) * 100, 2)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None
