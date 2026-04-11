"""yfinance adapter — fills the gaps screener.in doesn't expose.

yfinance is the *fallback* for Indian equities: screener wins for every
ratio it provides (see `_SCREENER_WINS` in source.py), but yfinance
covers the holes — daily price history for the dip detector, current /
quick ratios (screener's aggregated balance sheet doesn't split current
vs non-current), net debt / EBITDA, and the sector / industry strings
plus the analyst bundle.

`.info` and `.fast_info` are rate-limited in 2026 so we stick to the
DataFrame accessors (`.history`, `.balance_sheet`, `.income_stmt`,
`.cashflow`, `.quarterly_income_stmt`) and only touch `.info` as a
best-effort final step for metadata.

Tests monkeypatch `_fetch_yfinance_sync` on this module directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, TypeVar

from data_sources.fundamentals_source.snapshot import SnapshotFields

if TYPE_CHECKING:
    # Forward references — only the type checker sees these. No runtime
    # import cost because `from __future__ import annotations` makes every
    # type hint a string that's only resolved by mypy / pyright.
    from pandas import DataFrame
    from yfinance import Ticker

log = logging.getLogger(__name__)

PriceRow = tuple[str, float]

_T = TypeVar("_T")

_NSE_SUFFIX = ".NS"
_BSE_SUFFIX = ".BO"


def _yf_symbol(raw: str) -> str:
    """Format a bare ticker as a yfinance symbol ('RELIANCE' → 'RELIANCE.NS').

    Kept private to this adapter — the `.NS` / `.BO` suffix convention is
    a yfinance implementation detail and shouldn't leak upward into the
    orchestrator layer. (Previously this lived in `fundamental_engine.
    ticker` which created a layering violation: a low-level data source
    shouldn't depend on the high-level engine package.)
    """
    t = raw.strip().upper()
    if t.endswith(_NSE_SUFFIX) or t.endswith(_BSE_SUFFIX):
        return t
    return f"{t}{_NSE_SUFFIX}"


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

    hist: "DataFrame | None" = _safe(
        lambda: t.history(period="1y", auto_adjust=True),
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
    _fill_balance_sheet_derived(out, bs)
    _fill_income_statement_derived(out, is_, bs)
    _fill_cash_flow_derived(out, cf, is_)
    _fill_working_capital_days(out, is_, bs)
    _fill_asset_composition(out, bs)
    _fill_quarterly_trend(out, q_is)
    _fill_info_metadata(out, t, ticker)

    return out, price_history


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
    out["current_price"] = float(closes.iloc[-1])
    out["high_52w"] = float(hist["High"].max())
    out["low_52w"] = float(hist["Low"].min())
    for ts, close in closes.items():
        try:
            date_str = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
            price_history.append((date_str, float(close)))
        except Exception:
            continue


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
    ebit = _bs_first(is_, ("EBIT", "Operating Income"))
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

    # Interest coverage is only meaningful when EBIT is positive — a loss-
    # making company has no "coverage" regardless of the numeric ratio.
    if ebit is not None and ebit > 0 and interest is not None and interest != 0:
        out["interest_coverage"] = round(ebit / abs(interest), 2)

    # Prefer direct Net Debt row; else compute from total_debt − cash.
    computed_net_debt: float | None = None
    if net_debt_raw is not None:
        computed_net_debt = net_debt_raw
    elif total_debt is not None and cash is not None:
        computed_net_debt = total_debt - cash
    if computed_net_debt is not None and ebitda is not None and ebitda > 0:
        out["net_debt_to_ebitda"] = round(computed_net_debt / ebitda, 2)

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
    if q_rev:
        out["quarterly_revenue"] = q_rev
        out["quarterly_dates"] = q_dates
    if q_pat:
        out["quarterly_pat"] = q_pat
    if len(q_rev) >= 5 and q_rev[4] > 0:
        out["latest_yoy_revenue_growth"] = round(
            (q_rev[0] - q_rev[4]) / q_rev[4] * 100, 2
        )
    if len(q_pat) >= 5 and q_pat[4] > 0:
        out["latest_yoy_pat_growth"] = round(
            (q_pat[0] - q_pat[4]) / q_pat[4] * 100, 2
        )


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


# ── DataFrame helpers ────────────────────────────────────────────────────────


def _extract_quarterly_series(
    q_is: "DataFrame | None",
) -> tuple[list[float], list[float], list[str]]:
    """Return (revenue, net_income, date_iso) lists, most-recent-first."""
    if q_is is None:
        return [], [], []
    try:
        if q_is.empty:
            return [], [], []
    except Exception:
        return [], [], []

    rev_row = None
    pat_row = None
    for name in ("Total Revenue", "Operating Revenue"):
        if name in q_is.index:
            rev_row = q_is.loc[name]
            break
    for name in ("Net Income", "Net Income Common Stockholders"):
        if name in q_is.index:
            pat_row = q_is.loc[name]
            break

    revenue_series: list[float] = []
    pat_series: list[float] = []
    dates: list[str] = []

    if rev_row is not None:
        for col, val in rev_row.items():
            try:
                if val is None:
                    continue
                v = float(val)
                if v != v:  # NaN
                    continue
                revenue_series.append(v)
                dates.append(
                    col.date().isoformat() if hasattr(col, "date") else str(col)[:10]
                )
            except (TypeError, ValueError):
                continue

    if pat_row is not None:
        for col, val in pat_row.items():
            try:
                if val is None:
                    continue
                v = float(val)
                if v != v:
                    continue
                pat_series.append(v)
            except (TypeError, ValueError):
                continue

    return revenue_series, pat_series, dates


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
            if len(series) < years + 1:
                continue
            current = float(series.iloc[0])
            past = float(series.iloc[years])
            if past <= 0 or current <= 0:
                continue
            return round((((current / past) ** (1 / years)) - 1) * 100, 2)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None
