"""Fundamentals data source — live yfinance + screener.in.

Builds a rich `BalanceSheetSnapshot` from live API calls:

  Scored ratios (fed to `scorers/fundamentals_scorer.py`)
    - Leverage / liquidity: debt_to_equity, interest_coverage,
      current_ratio, quick_ratio, net_debt_to_ebitda
    - Profitability:        roe, roce, roa
    - Growth:               rev_3y_cagr, pat_3y_cagr
    - Earnings quality:     ocf_to_pat, fcf_to_pat
    - Promoter:             promoter_holding, pledge_pct

  Financial profile (not scored — surfaced in the report so the reader
  can see where the company is actually spending and earning)
    - Margin stack:         gross_margin, operating_margin, net_margin
    - Cost structure:       cogs_to_revenue, sga_to_revenue, tax_rate
    - Working capital:      debtor_days, inventory_days, payable_days,
                            cash_conversion_cycle_days
    - Asset composition:    tangible_book_ratio, goodwill_to_equity
    - Quarterly trend:      quarterly_revenue[], quarterly_pat[],
                            quarterly_dates[], latest_yoy_revenue_growth,
                            latest_yoy_pat_growth

yfinance's .info / .fast_info are rate-limited in 2026 — we use
.history(), .balance_sheet, .income_stmt, .cashflow, .quarterly_income_stmt
instead. screener.in adds ROE / ROCE / promoter holding / pledge (more
reliable for Indian equities than statement-derived arithmetic).

No fixtures, no hardcoded data — every snapshot is built fresh at fetch time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)

SCORED_FIELDS: tuple[str, ...] = (
    "debt_to_equity",
    "interest_coverage",
    "current_ratio",
    "quick_ratio",
    "net_debt_to_ebitda",
    "roe",
    "roce",
    "roa",
    "rev_3y_cagr",
    "pat_3y_cagr",
    "ocf_to_pat",
    "fcf_to_pat",
    "promoter_holding",
    "pledge_pct",
)

SCREENER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SCREENER_BASE_URL = "https://www.screener.in/company"
HTTP_TIMEOUT_SECONDS = 20.0

# Fields where screener.in is authoritative (pre-computed, India-specific).
# yfinance's "Total Debt" includes operating leases and other interest-bearing
# items that Indian analysts exclude; screener's "Borrowings" row matches what
# Indian investors actually use. Same story for OCF / FCF / working capital
# days — screener's definitions are the canonical ones.
_SCREENER_WINS: frozenset[str] = frozenset({
    "roe",
    "roce",
    "roa",
    "promoter_holding",
    "pledge_pct",
    "market_cap_cr",
    "debt_to_equity",
    "interest_coverage",
    "ocf_to_pat",
    "fcf_to_pat",
    "rev_3y_cagr",
    "pat_3y_cagr",
    "operating_margin",
    "net_margin",
    "tax_rate",
    "debtor_days",
    "inventory_days",
    "payable_days",
    "cash_conversion_cycle_days",
    "pe_ratio",
    "book_value_per_share",
    "dividend_yield",
    # yfinance often drops a middle quarter for recent IPOs — screener's
    # per-company RSS-like quarters table is more complete for India.
    "quarterly_revenue",
    "quarterly_pat",
    "quarterly_dates",
    "latest_yoy_revenue_growth",
    "latest_yoy_pat_growth",
    # Bank-specific metrics — always from screener's P&L / BS structure.
    "nim",
    "net_interest_income_cr",
    "non_interest_income_cr",
    "non_interest_income_ratio",
    "deposits_cr",
    "borrowings_cr",
    "credit_deposit_ratio",
    "is_bank",
    # Shareholding trend — 12 quarters of FII / DII / promoter / public.
    "shareholding_dates",
    "promoter_holding_trend",
    "fii_holding_trend",
    "dii_holding_trend",
    "public_holding_trend",
    "num_shareholders_trend",
    "fii_delta_1y_pct_points",
    "dii_delta_1y_pct_points",
    "promoter_delta_1y_pct_points",
    "num_shareholders_delta_1y_pct",
    # Multi-year ratio / P&L trend series.
    "roce_annual_trend",
    "operating_margin_annual_trend",
    "net_profit_annual_trend",
    "sales_annual_trend",
    "borrowings_annual_trend",
    "borrowings_delta_3y_pct",
    # Full raw dumps — always from screener (yfinance doesn't expose these).
    "raw_profit_loss",
    "raw_profit_loss_headers",
    "raw_balance_sheet",
    "raw_balance_sheet_headers",
    "raw_cash_flow",
    "raw_cash_flow_headers",
    "raw_ratios",
    "raw_ratios_headers",
    "raw_quarters",
    "raw_quarters_headers",
    "raw_shareholding_quarterly",
    "raw_shareholding_headers",
    "peers",
    "documents",
    "screener_analysis_text",
})

PriceRow = tuple[str, float]  # (ISO date, adjusted close)

# Sector keywords that route a snapshot to the bank-specific rubric and
# display path. The canonical signal is `snap.is_bank` (set by the screener
# parser when the P&L has the bank row shape); these strings are the fallback
# for snapshots where the parser didn't fire (e.g. yfinance-only fetches).
_BANK_SECTOR_KEYWORDS: tuple[str, ...] = (
    "bank",
    "nbfc",
    "insurance",
    "asset management",
    "financial services",
    "capital markets",
)


def is_bank_snapshot(snap: "BalanceSheetSnapshot") -> bool:
    """Shared sector check used by scorer, CLI, and report layers."""
    if snap.is_bank:
        return True
    haystack = f"{snap.sector or ''} {snap.industry or ''}".lower()
    return any(kw in haystack for kw in _BANK_SECTOR_KEYWORDS)


@dataclass(slots=True)
class BalanceSheetSnapshot:
    ticker: str
    as_of_date: str

    # ── Scored: leverage / liquidity ──────────────────────────────────────────
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    net_debt_to_ebitda: float | None = None

    # ── Scored: profitability ─────────────────────────────────────────────────
    roe: float | None = None
    roce: float | None = None
    roa: float | None = None

    # ── Scored: growth ────────────────────────────────────────────────────────
    rev_3y_cagr: float | None = None
    pat_3y_cagr: float | None = None

    # ── Scored: earnings quality ──────────────────────────────────────────────
    ocf_to_pat: float | None = None
    fcf_to_pat: float | None = None

    # ── Scored: promoter ──────────────────────────────────────────────────────
    promoter_holding: float | None = None
    pledge_pct: float | None = None

    # ── Price context (informational) ─────────────────────────────────────────
    current_price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap_cr: float | None = None
    pe_ratio: float | None = None
    book_value_per_share: float | None = None
    dividend_yield: float | None = None

    # ── Financial profile: margins (informational) ───────────────────────────
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None

    # ── Financial profile: cost structure (informational) ────────────────────
    cogs_to_revenue: float | None = None
    sga_to_revenue: float | None = None
    tax_rate: float | None = None

    # ── Financial profile: working capital days (informational) ──────────────
    debtor_days: float | None = None
    inventory_days: float | None = None
    payable_days: float | None = None
    cash_conversion_cycle_days: float | None = None

    # ── Financial profile: asset composition (informational) ─────────────────
    tangible_book_ratio: float | None = None  # tangible BV / equity
    goodwill_to_equity: float | None = None  # intangibles / equity

    # ── Bank-specific metrics (populated only for banks / NBFCs) ─────────────
    # NIM = (Interest Income − Interest Expense) / Total Assets. Closer to
    # reality than (NII / average interest-earning assets) but uses only
    # what screener exposes so no cross-period mismatch.
    nim: float | None = None  # %
    net_interest_income_cr: float | None = None  # ₹ Cr (annual, latest FY)
    non_interest_income_cr: float | None = None  # ₹ Cr — fee + treasury
    non_interest_income_ratio: float | None = None  # % of total income
    deposits_cr: float | None = None
    borrowings_cr: float | None = None
    credit_deposit_ratio: float | None = None
    is_bank: bool = False

    # ── Shareholding trend (quarterly, newest-first) ─────────────────────────
    # Captures the smart-money / domestic-money tug of war that often
    # explains why a name is trending up or down before the fundamentals
    # catch up. All series are aligned with `shareholding_dates`.
    shareholding_dates: list[str] = field(default_factory=list)
    promoter_holding_trend: list[float] = field(default_factory=list)  # %
    fii_holding_trend: list[float] = field(default_factory=list)  # %
    dii_holding_trend: list[float] = field(default_factory=list)  # %
    public_holding_trend: list[float] = field(default_factory=list)  # %
    num_shareholders_trend: list[float] = field(default_factory=list)  # raw count
    # 1-year deltas (most-recent − 4 quarters back). None if insufficient history.
    fii_delta_1y_pct_points: float | None = None
    dii_delta_1y_pct_points: float | None = None
    promoter_delta_1y_pct_points: float | None = None
    num_shareholders_delta_1y_pct: float | None = None

    # ── Multi-year ratio / P&L trend (oldest→newest, annual) ─────────────────
    # Raw series so the report can render direction (↑/↓/→) and Claude can
    # reason about "is the business improving or degrading?" without just
    # looking at the latest snapshot.
    roce_annual_trend: list[float] = field(default_factory=list)
    operating_margin_annual_trend: list[float] = field(default_factory=list)
    net_profit_annual_trend: list[float] = field(default_factory=list)
    sales_annual_trend: list[float] = field(default_factory=list)
    borrowings_annual_trend: list[float] = field(default_factory=list)
    borrowings_delta_3y_pct: float | None = None  # + = debt grew, − = deleveraged

    # ── Full raw screener dump (everything we parsed, keyed by row label) ────
    # Each section is {row_label: [v0, ..., vN]} oldest→newest with None gaps.
    # Downstream code (scorers, reports, Claude) can ask any question about
    # any line item without the parser having to learn new labels first.
    # Headers are stored alongside so callers can align values to dates.
    raw_profit_loss: dict[str, list[float | None]] = field(default_factory=dict)
    raw_profit_loss_headers: list[str] = field(default_factory=list)
    raw_balance_sheet: dict[str, list[float | None]] = field(default_factory=dict)
    raw_balance_sheet_headers: list[str] = field(default_factory=list)
    raw_cash_flow: dict[str, list[float | None]] = field(default_factory=dict)
    raw_cash_flow_headers: list[str] = field(default_factory=list)
    raw_ratios: dict[str, list[float | None]] = field(default_factory=dict)
    raw_ratios_headers: list[str] = field(default_factory=list)
    raw_quarters: dict[str, list[float | None]] = field(default_factory=dict)
    raw_quarters_headers: list[str] = field(default_factory=list)
    raw_shareholding_quarterly: dict[str, list[float | None]] = field(default_factory=dict)
    raw_shareholding_headers: list[str] = field(default_factory=list)

    # ── Peer comparison and documents (from screener) ────────────────────────
    peers: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, str]] = field(default_factory=list)
    screener_analysis_text: str = ""

    # ── yfinance extras (analyst bundle, metadata — best-effort) ─────────────
    analyst_target_mean: float | None = None
    analyst_target_high: float | None = None
    analyst_target_low: float | None = None
    analyst_recommendation_mean: float | None = None  # 1 = strong buy, 5 = strong sell
    analyst_opinion_count: int | None = None
    country: str = ""
    website: str = ""
    full_time_employees: int | None = None
    business_summary: str = ""  # yfinance longBusinessSummary

    # ── Financial profile: quarterly trend (informational) ───────────────────
    quarterly_revenue: list[float] = field(default_factory=list)  # newest-first
    quarterly_pat: list[float] = field(default_factory=list)
    quarterly_dates: list[str] = field(default_factory=list)
    latest_yoy_revenue_growth: float | None = None
    latest_yoy_pat_growth: float | None = None

    # ── Metadata ──────────────────────────────────────────────────────────────
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    source: str = "unknown"
    field_sources: dict[str, str] = field(default_factory=dict)

    @property
    def data_completeness_pct(self) -> float:
        present = sum(1 for f in SCORED_FIELDS if getattr(self, f) is not None)
        return round(100.0 * present / len(SCORED_FIELDS), 1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BalanceSheetSnapshot":
        allowed = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in data.items() if k in allowed}
        kwargs.setdefault("ticker", "")
        kwargs.setdefault("as_of_date", date.today().isoformat())
        return cls(**kwargs)


@dataclass(slots=True)
class FetchResult:
    """Everything a single yfinance + screener fetch produces."""

    snapshot: BalanceSheetSnapshot
    price_history: list[PriceRow] = field(default_factory=list)


class FundamentalsSource:
    """Async facade over live yfinance + screener.in."""

    async def fetch(self, ticker: str) -> BalanceSheetSnapshot:
        result = await self.fetch_full(ticker)
        return result.snapshot

    async def fetch_full(self, ticker: str) -> FetchResult:
        bare = ticker.strip().upper()
        log.info("fundamentals: live fetch for %s", bare)

        yf_task = _fetch_yfinance(bare)
        screener_task = _fetch_screener_in(bare)
        yf_result, screener_data = await asyncio.gather(
            yf_task, screener_task, return_exceptions=True
        )

        yf_data: dict[str, Any]
        price_history: list[PriceRow]
        if isinstance(yf_result, Exception):
            log.warning("yfinance failed for %s: %s", bare, yf_result)
            yf_data, price_history = {}, []
        else:
            yf_data, price_history = yf_result

        if isinstance(screener_data, Exception):
            log.warning("screener.in failed for %s: %s", bare, screener_data)
            screener_data = {}

        merged = _merge(yf_data, screener_data)
        merged["ticker"] = bare
        merged["as_of_date"] = date.today().isoformat()
        merged["source"] = _source_tag(yf_data, screener_data)

        allowed = set(BalanceSheetSnapshot.__dataclass_fields__)
        kwargs = {k: v for k, v in merged.items() if k in allowed}
        snap = BalanceSheetSnapshot(**kwargs)
        snap.field_sources = _field_sources(yf_data, screener_data)
        return FetchResult(snapshot=snap, price_history=price_history)


# ── yfinance adapter ──────────────────────────────────────────────────────────


async def _fetch_yfinance(ticker: str) -> tuple[dict[str, Any], list[PriceRow]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_yfinance_sync, ticker)


def _fetch_yfinance_sync(ticker: str) -> tuple[dict[str, Any], list[PriceRow]]:
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed")
        return {}, []

    from fundamental_engine.ticker import yf_symbol

    symbol = yf_symbol(ticker)

    try:
        t = yf.Ticker(symbol)
    except Exception as e:
        log.warning("yfinance Ticker() failed for %s: %s", ticker, e)
        return {}, []

    hist = _safe(lambda: t.history(period="1y", auto_adjust=True), label=f"history {ticker}")
    bs = _safe(lambda: t.balance_sheet, label=f"balance_sheet {ticker}")
    is_ = _safe(lambda: t.income_stmt, label=f"income_stmt {ticker}")
    cf = _safe(lambda: t.cashflow, label=f"cashflow {ticker}")
    q_is = _safe(
        lambda: t.quarterly_income_stmt, label=f"quarterly_income_stmt {ticker}"
    )

    out: dict[str, Any] = {}
    price_history: list[PriceRow] = []

    # Price context + daily closes.
    if hist is not None and not hist.empty:
        closes = hist["Close"].dropna()
        if len(closes) > 0:
            out["current_price"] = float(closes.iloc[-1])
            out["high_52w"] = float(hist["High"].max())
            out["low_52w"] = float(hist["Low"].min())
            for ts, close in closes.items():
                try:
                    date_str = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
                    price_history.append((date_str, float(close)))
                except Exception:
                    continue

    # ── Leverage / liquidity ──────────────────────────────────────────────────
    total_debt = _bs_first(bs, ("Total Debt", "Long Term Debt"))
    net_debt = _bs_first(bs, ("Net Debt",))
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
    current_assets = _bs_first(bs, ("Current Assets",))
    current_liabilities = _bs_first(
        bs, ("Current Liabilities", "Total Current Liabilities")
    )
    inventory = _bs_first(bs, ("Inventory",))
    total_assets = _bs_first(bs, ("Total Assets",))
    tangible_book = _bs_first(bs, ("Tangible Book Value", "Net Tangible Assets"))
    goodwill_intangibles = _bs_first(
        bs,
        (
            "Goodwill And Other Intangible Assets",
            "Other Intangible Assets",
            "Goodwill",
        ),
    )
    accounts_receivable = _bs_first(bs, ("Accounts Receivable", "Receivables"))
    accounts_payable = _bs_first(bs, ("Accounts Payable", "Payables"))

    if total_debt is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = round(total_debt / equity, 3)

    if current_assets is not None and current_liabilities is not None and current_liabilities > 0:
        out["current_ratio"] = round(current_assets / current_liabilities, 3)
        if inventory is not None:
            out["quick_ratio"] = round(
                (current_assets - inventory) / current_liabilities, 3
            )

    # ── Income statement ──────────────────────────────────────────────────────
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

    if ebit is not None and ebit > 0 and interest is not None and interest != 0:
        out["interest_coverage"] = round(ebit / abs(interest), 2)

    # Prefer direct Net Debt row from yfinance; fall back to total_debt - cash.
    computed_net_debt: float | None = None
    if net_debt is not None:
        computed_net_debt = net_debt
    elif total_debt is not None and cash is not None:
        computed_net_debt = total_debt - cash
    if (
        computed_net_debt is not None
        and ebitda is not None
        and ebitda > 0
    ):
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

    # Margin stack
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

    if (
        pretax is not None
        and pretax > 0
        and tax_provision is not None
    ):
        out["tax_rate"] = round(tax_provision / pretax * 100, 2)

    # ── Cash flow: OCF/PAT + FCF/PAT ──────────────────────────────────────────
    ocf = _bs_first(
        cf,
        (
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
        ),
    )
    fcf = _bs_first(cf, ("Free Cash Flow",))
    capex = _bs_first(cf, ("Capital Expenditure",))

    if ocf is not None and net_income is not None and net_income > 0:
        out["ocf_to_pat"] = round(ocf / net_income, 3)

    # Prefer Free Cash Flow row; else compute from OCF + capex (capex is negative).
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf + capex  # capex < 0, so this subtracts
    if fcf is not None and net_income is not None and net_income > 0:
        out["fcf_to_pat"] = round(fcf / net_income, 3)

    # ── Working capital days ──────────────────────────────────────────────────
    if revenue is not None and revenue > 0 and accounts_receivable is not None:
        out["debtor_days"] = round(accounts_receivable / revenue * 365, 1)
    if cogs is not None and cogs != 0:
        cogs_abs = abs(cogs)
        if inventory is not None:
            out["inventory_days"] = round(inventory / cogs_abs * 365, 1)
        if accounts_payable is not None:
            out["payable_days"] = round(accounts_payable / cogs_abs * 365, 1)
    if all(out.get(k) is not None for k in ("debtor_days", "inventory_days", "payable_days")):
        out["cash_conversion_cycle_days"] = round(
            out["debtor_days"] + out["inventory_days"] - out["payable_days"], 1
        )

    # ── Asset composition ────────────────────────────────────────────────────
    if tangible_book is not None and equity is not None and equity > 0:
        out["tangible_book_ratio"] = round(tangible_book / equity, 3)
    if goodwill_intangibles is not None and equity is not None and equity > 0:
        out["goodwill_to_equity"] = round(goodwill_intangibles / equity, 3)

    # ── Quarterly trend ───────────────────────────────────────────────────────
    q_rev, q_pat, q_dates = _extract_quarterly_series(q_is)
    if q_rev:
        out["quarterly_revenue"] = q_rev
        out["quarterly_dates"] = q_dates
    if q_pat:
        out["quarterly_pat"] = q_pat

    # Latest YoY growth: compare q[0] with q[4] when we have at least 5 quarters.
    if len(q_rev) >= 5 and q_rev[4] > 0:
        out["latest_yoy_revenue_growth"] = round(
            (q_rev[0] - q_rev[4]) / q_rev[4] * 100, 2
        )
    if len(q_pat) >= 5 and q_pat[4] > 0:
        out["latest_yoy_pat_growth"] = round(
            (q_pat[0] - q_pat[4]) / q_pat[4] * 100, 2
        )

    # Metadata (best effort — .info is often rate limited).
    info = _safe(lambda: t.info or {}, label=f"info {ticker}") or {}
    if info:
        out.setdefault("company_name", info.get("longName") or info.get("shortName") or "")
        out.setdefault("sector", info.get("sector") or "")
        out.setdefault("industry", info.get("industry") or "")
        out.setdefault("country", info.get("country") or "")
        out.setdefault("website", info.get("website") or "")
        fte = info.get("fullTimeEmployees")
        if fte is not None:
            out.setdefault("full_time_employees", int(fte))
        summary = info.get("longBusinessSummary") or info.get("description") or ""
        if summary:
            out.setdefault("business_summary", str(summary)[:3000])
        # Analyst bundle — these are the consensus targets and recommendation
        # that Bloomberg / Reuters terminals expose for institutional users.
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

    return out, price_history


def _extract_quarterly_series(
    q_is,
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
                dates.append(col.date().isoformat() if hasattr(col, "date") else str(col)[:10])
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


def _safe(fn, *, label: str):
    try:
        return fn()
    except Exception as e:
        log.debug("yfinance %s raised %s", label, e)
        return None


def _bs_first(df, candidates: tuple[str, ...]) -> float | None:
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
    df, candidates: tuple[str, ...], *, years: int
) -> float | None:
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


# ── screener.in adapter ───────────────────────────────────────────────────────
#
# The screener.in HTML has a fixed set of tables per company:
#   #top-ratios       — P/E, P/B, ROE, ROCE, market cap, div yield, D/E
#   #profit-loss      — annual P&L rows (Sales/Revenue, Interest, NP, Tax%, ...)
#   #balance-sheet    — annual BS rows (Borrowings, Reserves, Total Assets, ...)
#   #cash-flow        — annual CF rows (CFO, CFI, CFF, FCF, CFO/OP)
#   #ratios           — 10-year Debtor / Inventory / Payable days + ROCE%
#   #quarters         — 13 quarters of P&L rows (Sales, OP, Interest, NP, ...)
#   #shareholding     — quarterly + yearly Promoter/FII/DII/Public trend
#   #peers            — peer comparison (JS-loaded, static HTML is empty)
#   #documents        — annual reports / credit ratings / investor PPTs
#   #analysis         — screener's own narrative block
#
# `_fetch_screener_in` is the orchestrator. It parses each table once into
# `(headers, rows)` tuples, then fans them out to `_apply_*` functions that
# mutate a shared `out` dict. The raw tables are also stored verbatim on the
# snapshot via `_apply_raw_dumps` so future questions can be answered without
# re-parsing the page.


async def _fetch_screener_in(ticker: str) -> dict[str, Any]:
    urls = [
        f"{SCREENER_BASE_URL}/{ticker.upper()}/consolidated/",
        f"{SCREENER_BASE_URL}/{ticker.upper()}/",
    ]
    headers = {"User-Agent": SCREENER_USER_AGENT}

    resp = None
    for url in urls:
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                break
        except Exception as e:
            log.warning("screener.in fetch error (%s): %s", url, e)
            continue

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else "no response"
        log.warning("screener.in returned %s for %s", status, ticker)
        return {}

    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        log.warning("selectolax not installed — screener parsing skipped")
        return {}

    tree = HTMLParser(resp.text)
    out: dict[str, Any] = {}

    _parse_top_ratios(tree, out)
    _parse_h1(tree, out)
    _parse_shareholding(tree, out)

    # Parse all numeric tables once. Each returns (headers, rows) so we can
    # handle TTM columns correctly (drop them before computing CAGRs) and
    # align cells by position even when a cell is missing.
    bs_headers, bs_rows = _parse_section(tree, "balance-sheet")
    pl_headers, pl_rows = _parse_section(tree, "profit-loss")
    cf_headers, cf_rows = _parse_section(tree, "cash-flow")
    r_headers, r_rows = _parse_section(tree, "ratios")

    _apply_balance_sheet(out, bs_headers, bs_rows)
    _apply_profit_loss(out, pl_headers, pl_rows)
    _apply_cash_flow(out, cf_rows, pl_headers, pl_rows)
    _apply_ratios(out, r_rows)
    _apply_quarters(tree, out)
    _apply_derived(out, bs_rows, pl_headers, pl_rows)
    _apply_bank_metrics(out, bs_rows, pl_headers, pl_rows)
    _apply_multi_year_trends(out, bs_headers, bs_rows, pl_headers, pl_rows, r_rows)
    _apply_shareholding_trend(tree, out)
    _apply_raw_dumps(
        out,
        pl_headers, pl_rows,
        bs_headers, bs_rows,
        cf_headers, cf_rows,
        r_headers, r_rows,
        tree,
    )
    _apply_peers(tree, out)
    _apply_documents(tree, out)
    _apply_analysis_text(tree, out)

    return out


def _apply_raw_dumps(
    out: dict[str, Any],
    pl_headers: list[str], pl_rows: dict[str, list[float | None]],
    bs_headers: list[str], bs_rows: dict[str, list[float | None]],
    cf_headers: list[str], cf_rows: dict[str, list[float | None]],
    r_headers: list[str], r_rows: dict[str, list[float | None]],
    tree,
) -> None:
    """Store everything we parsed so future questions don't need re-scraping.

    This is the "capture all of it" layer — whatever row screener shows
    in P&L / BS / Cash flow / Ratios / Quarters lives here verbatim,
    including rows the typed fields don't cover. Downstream code
    (scorers, reports, Claude) can look up any line item by its
    lowercased label without the parser having to learn it first.
    """
    out["raw_profit_loss"] = pl_rows
    out["raw_profit_loss_headers"] = pl_headers
    out["raw_balance_sheet"] = bs_rows
    out["raw_balance_sheet_headers"] = bs_headers
    out["raw_cash_flow"] = cf_rows
    out["raw_cash_flow_headers"] = cf_headers
    out["raw_ratios"] = r_rows
    out["raw_ratios_headers"] = r_headers
    q_headers, q_rows = _parse_section(tree, "quarters")
    out["raw_quarters"] = q_rows
    out["raw_quarters_headers"] = q_headers
    sh_el = tree.css_first("#shareholding")
    if sh_el is not None:
        q_sh = sh_el.css_first("#quarterly-shp")
        if q_sh is not None:
            tbl = q_sh.css_first("table")
            if tbl is not None:
                raw_headers = [
                    th.text(strip=True) for th in tbl.css("thead th")
                ]
                headers = [h for h in raw_headers if h]
                out["raw_shareholding_headers"] = headers
                rows: dict[str, list[float | None]] = {}
                for row in tbl.css("tbody tr"):
                    cells = row.css("td")
                    if not cells:
                        continue
                    raw_label = cells[0].text(strip=True)
                    if not raw_label:
                        continue
                    label = raw_label.lower().rstrip("+").strip()
                    values: list[float | None] = []
                    for c in cells[1:]:
                        text = c.text(strip=True)
                        if not text or text == "--":
                            values.append(None)
                            continue
                        v = _parse_pct(text)
                        if v is None:
                            v = _parse_float(text)
                        values.append(v)
                    if any(v is not None for v in values):
                        rows[label] = values
                out["raw_shareholding_quarterly"] = rows


def _apply_peers(tree, out: dict[str, Any]) -> None:
    """Extract the peer-comparison table (relative valuation vs industry)."""
    peers_el = tree.css_first("#peers")
    if peers_el is None:
        return
    # Screener loads peers via JS into a placeholder table, so the DOM
    # may contain either a real table or just `#peers-table-placeholder`.
    # Look for any actual data-table inside the peers section.
    tbl = peers_el.css_first("table")
    if tbl is None:
        return
    header_cells = [th.text(strip=True) for th in tbl.css("thead th")]
    if not header_cells:
        return
    records: list[dict[str, Any]] = []
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if len(cells) < 2:
            continue
        rec: dict[str, Any] = {}
        for i, cell in enumerate(cells):
            key = header_cells[i].lower() if i < len(header_cells) else f"col{i}"
            text = cell.text(strip=True)
            if not text:
                continue
            parsed = _parse_float(text.rstrip("%").replace(",", ""))
            rec[key] = parsed if parsed is not None else text
        if rec:
            records.append(rec)
    if records:
        out["peers"] = records


def _apply_documents(tree, out: dict[str, Any]) -> None:
    """Extract links to annual reports, credit ratings, investor PPTs."""
    docs_el = tree.css_first("#documents")
    if docs_el is None:
        return
    records: list[dict[str, str]] = []
    for a in docs_el.css("a[href]")[:40]:  # cap to avoid ballooning
        href = a.attributes.get("href", "") or ""
        label = a.text(strip=True)
        if not href or not label:
            continue
        # Skip pure navigation links
        if href.startswith("#"):
            continue
        records.append({"label": label[:200], "url": href})
    if records:
        out["documents"] = records


def _apply_analysis_text(tree, out: dict[str, Any]) -> None:
    """Capture screener's own `#analysis` narrative block, if present."""
    el = tree.css_first("#analysis")
    if el is None:
        return
    text = el.text(separator=" ", strip=True)
    if text:
        # Trim to a reasonable cap — anything longer is usually boilerplate.
        out["screener_analysis_text"] = text[:3000]


def _parse_top_ratios(tree, out: dict[str, Any]) -> None:
    for item in tree.css("ul#top-ratios li"):
        name_el = item.css_first(".name")
        val_el = item.css_first(".number")
        if not (name_el and val_el):
            continue
        name = name_el.text(strip=True).lower()
        raw = val_el.text(strip=True)
        if "roce" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["roce"] = v
        elif "roe" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["roe"] = v
        elif "market cap" in name:
            v = _parse_float(raw)
            if v is not None:
                out["market_cap_cr"] = v
        elif "current price" in name:
            v = _parse_float(raw)
            if v is not None:
                out["current_price"] = v
        elif "stock p/e" in name or name == "p/e":
            v = _parse_float(raw)
            if v is not None and v > 0:
                out["pe_ratio"] = v
        elif "book value" in name:
            v = _parse_float(raw)
            if v is not None:
                out["book_value_per_share"] = v
        elif "dividend yield" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["dividend_yield"] = v
        elif "debt to equity" in name or name.replace(" ", "") == "d/e":
            v = _parse_float(raw)
            if v is not None and v >= 0:
                out["debt_to_equity"] = v
        elif "high / low" in name:
            # Screener shows e.g. "580 / 328" — pick both.
            parts = [p.strip() for p in raw.split("/")]
            if len(parts) == 2:
                hi = _parse_float(parts[0])
                lo = _parse_float(parts[1])
                if hi is not None:
                    out["high_52w"] = hi
                if lo is not None:
                    out["low_52w"] = lo


def _parse_h1(tree, out: dict[str, Any]) -> None:
    h1 = tree.css_first("h1")
    if h1:
        text = h1.text(strip=True)
        if text:
            out["company_name"] = text


def _parse_shareholding(tree, out: dict[str, Any]) -> None:
    sh = tree.css_first("#shareholding")
    if sh is None:
        return
    for row in sh.css("table tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        label = cells[0].text(strip=True).lower().rstrip("+").strip()
        vals = [c.text(strip=True) for c in cells[1:]]
        if not vals:
            continue
        latest = _parse_pct(vals[-1])
        if latest is None:
            continue
        if label.startswith("promoter") and "pledge" not in label:
            out["promoter_holding"] = latest
        elif "pledge" in label:
            out["pledge_pct"] = latest
    if "promoter_holding" in out and "pledge_pct" not in out:
        out["pledge_pct"] = 0.0


def _parse_section(
    tree, section_id: str
) -> tuple[list[str], dict[str, list[float | None]]]:
    """Return (headers, {row_label: [v0, ..., vN]}) for a screener table.

    Headers exclude the empty first cell. Row values are positional: each
    index corresponds to the same index in `headers`, with `None` for
    missing/non-numeric cells. Trailing `+` markers on labels are stripped
    and labels lowercased. This lets callers drop a TTM column (or any
    other named column) without shifting the CAGR window.
    """
    el = tree.css_first(f"#{section_id}")
    if el is None:
        return [], {}
    tbl = el.css_first("table")
    if tbl is None:
        return [], {}

    raw_headers = [th.text(strip=True) for th in tbl.css("thead th")]
    headers = [h for h in raw_headers if h]  # drop empty label column

    rows: dict[str, list[float | None]] = {}
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        raw_label = cells[0].text(strip=True)
        if not raw_label:
            continue
        label = raw_label.lower().rstrip("+").strip()
        values: list[float | None] = []
        for c in cells[1:]:
            text = c.text(strip=True)
            if not text or text == "--":
                values.append(None)
                continue
            parsed = _parse_float(text.rstrip("%"))
            values.append(parsed)
        if any(v is not None for v in values):
            rows[label] = values
    return headers, rows


def _drop_ttm(
    headers: list[str], values: list[float | None] | None
) -> list[float | None]:
    """Return `values` with any TTM column trimmed off.

    Screener appends a `TTM` column to P&L tables for most Indian companies.
    TTM mixes a partial fiscal year with a trailing-12-months window, so
    `values[-1]` is newer than `values[-2]` by ~6 months rather than 12 —
    using it as the terminal point of a 3-year CAGR yields a nonsense
    window (e.g. Mar-23 → TTM-25.5 ÷ 3, underestimating growth).
    """
    if not values:
        return []
    if headers and headers[-1].strip().upper() == "TTM":
        return values[:-1]
    return values


def _apply_balance_sheet(
    out: dict[str, Any],
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    if not rows:
        return
    # Screener uses "Borrowings+" for most companies and bare "Borrowing" for
    # banks / NBFCs. Also fall back to "total debt" for good measure.
    borrowings = _latest(
        rows.get("borrowings")
        or rows.get("borrowing")
        or rows.get("total debt")
    )
    equity_capital = _latest(rows.get("equity capital"))
    reserves = _latest(rows.get("reserves"))

    equity = None
    if equity_capital is not None and reserves is not None:
        equity = equity_capital + reserves

    if borrowings is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = round(borrowings / equity, 3)


def _apply_profit_loss(
    out: dict[str, Any],
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    if not rows:
        return

    sales = rows.get("sales") or rows.get("revenue")
    operating_profit = rows.get("operating profit")
    interest = rows.get("interest")
    net_profit = rows.get("net profit")
    tax_pct = rows.get("tax %")
    expenses = rows.get("expenses")

    latest_sales = _latest(sales)
    latest_op = _latest(operating_profit)
    latest_int = _latest(interest)
    latest_np = _latest(net_profit)
    latest_tax = _latest(tax_pct)

    if latest_op is not None and latest_int is not None and latest_int > 0:
        out["interest_coverage"] = round(latest_op / latest_int, 2)

    if latest_sales is not None and latest_sales > 0:
        if latest_op is not None:
            out["operating_margin"] = round(latest_op / latest_sales * 100, 2)
        if latest_np is not None:
            out["net_margin"] = round(latest_np / latest_sales * 100, 2)
        if expenses:
            latest_exp = _latest(expenses)
            if latest_exp is not None:
                out.setdefault(
                    "cogs_to_revenue",
                    round(latest_exp / latest_sales * 100, 2),
                )

    if latest_tax is not None and 0 <= latest_tax <= 100:
        out["tax_rate"] = latest_tax

    # CAGRs: drop TTM column first so the 3y window uses actual fiscal years.
    annual_sales = _drop_ttm(headers, sales)
    annual_np = _drop_ttm(headers, net_profit)

    cagr = _screener_cagr(annual_sales, years=3)
    if cagr is not None:
        out["rev_3y_cagr"] = cagr
    cagr = _screener_cagr(annual_np, years=3)
    if cagr is not None:
        out["pat_3y_cagr"] = cagr


def _apply_cash_flow(
    out: dict[str, Any],
    cf_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    if not cf_rows:
        return
    ocf = _latest(cf_rows.get("cash from operating activity"))
    fcf = _latest(cf_rows.get("free cash flow"))

    # Compare against the latest *annual* net profit, not TTM — cash-flow
    # rows are annual, so mixing periods would distort OCF/PAT and FCF/PAT.
    annual_np = _drop_ttm(pl_headers, pl_rows.get("net profit"))
    net_profit = _latest(annual_np)

    if ocf is not None and net_profit is not None and net_profit > 0:
        out["ocf_to_pat"] = round(ocf / net_profit, 3)
    if fcf is not None and net_profit is not None and net_profit > 0:
        out["fcf_to_pat"] = round(fcf / net_profit, 3)


def _apply_ratios(
    out: dict[str, Any],
    rows: dict[str, list[float | None]],
) -> None:
    if not rows:
        return
    dd = _latest(rows.get("debtor days"))
    idx = _latest(rows.get("inventory days"))
    dp = _latest(rows.get("days payable"))
    ccc = _latest(rows.get("cash conversion cycle"))
    if dd is not None:
        out["debtor_days"] = dd
    if idx is not None:
        out["inventory_days"] = idx
    if dp is not None:
        out["payable_days"] = dp
    if ccc is not None:
        out["cash_conversion_cycle_days"] = ccc


def _apply_quarters(tree, out: dict[str, Any]) -> None:
    headers, rows = _parse_section(tree, "quarters")
    sales = rows.get("sales") or rows.get("revenue")
    net_profit = rows.get("net profit")
    if not sales and not net_profit:
        return

    # Screener publishes rupees-in-crores; yfinance (the other path that
    # fills these fields) publishes rupees. Convert to rupees so the
    # downstream report renderer (which divides by 1e7 for Cr display)
    # works identically regardless of which source won.
    def cr_to_inr(vals: list[float | None]) -> list[float]:
        return [(v * 1e7) for v in vals if v is not None]

    # Oldest → newest in screener, we want most-recent-first.
    if sales:
        out["quarterly_revenue"] = list(reversed(cr_to_inr(sales)))
    if net_profit:
        out["quarterly_pat"] = list(reversed(cr_to_inr(net_profit)))

    if headers:
        iso_dates = [d for d in (_screener_month_to_iso(h) for h in headers) if d]
        if iso_dates:
            # Take only the most recent N dates matching the parsed values.
            n = max(
                len(out.get("quarterly_revenue", [])),
                len(out.get("quarterly_pat", [])),
            )
            out["quarterly_dates"] = list(reversed(iso_dates))[:n]

    rev = out.get("quarterly_revenue") or []
    if len(rev) >= 5 and rev[4] > 0:
        out["latest_yoy_revenue_growth"] = round(
            (rev[0] - rev[4]) / rev[4] * 100, 2
        )
    pat = out.get("quarterly_pat") or []
    if len(pat) >= 5 and pat[4] > 0:
        out["latest_yoy_pat_growth"] = round(
            (pat[0] - pat[4]) / pat[4] * 100, 2
        )


def _apply_derived(
    out: dict[str, Any],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """Cross-section derivations that need both balance sheet and P&L."""
    total_assets = _latest(bs_rows.get("total assets"))
    annual_np = _drop_ttm(pl_headers, pl_rows.get("net profit"))
    latest_np = _latest(annual_np)
    if total_assets is not None and latest_np is not None and total_assets > 0:
        out["roa"] = round(latest_np / total_assets * 100, 2)


def _apply_bank_metrics(
    out: dict[str, Any],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """Bank-specific metrics: NIM, NII, non-interest income, CD ratio.

    Triggers when the screener P&L has the bank shape (a `revenue` row
    for interest income, an `interest` row for interest expense, and
    either a `financing profit` line or a `deposits` row on the BS).
    Industrials use `sales` / `operating profit` instead, so this is a
    safe discriminator and sets `is_bank=True` for downstream dispatch.

    Screener's `expenses` row on bank P&Ls conflates operating expenses
    with loan-loss provisions so a formal Cost-to-Income cannot be
    derived cleanly from standard tables — we surface NII, non-interest
    income, and CD ratio which *are* directly computable.
    """
    if not (
        "revenue" in pl_rows
        and "interest" in pl_rows
        and ("financing profit" in pl_rows or "deposits" in bs_rows)
    ):
        return

    out["is_bank"] = True

    latest_revenue = _latest(_drop_ttm(pl_headers, pl_rows.get("revenue")))
    latest_interest = _latest(_drop_ttm(pl_headers, pl_rows.get("interest")))
    latest_other = _latest(_drop_ttm(pl_headers, pl_rows.get("other income")))

    total_assets = _latest(bs_rows.get("total assets"))
    deposits = _latest(bs_rows.get("deposits"))
    borrowings = _latest(
        bs_rows.get("borrowings")
        or bs_rows.get("borrowing")
        or bs_rows.get("total debt")
    )
    investments = _latest(bs_rows.get("investments"))
    fixed_assets = _latest(bs_rows.get("fixed assets"))

    if deposits is not None:
        out["deposits_cr"] = round(deposits, 0)
    if borrowings is not None:
        out["borrowings_cr"] = round(borrowings, 0)

    nii: float | None = None
    if latest_revenue is not None and latest_interest is not None:
        nii = latest_revenue - latest_interest
        out["net_interest_income_cr"] = round(nii, 0)

    # NIM uses Total Assets as the denominator — a real bank analyst uses
    # average interest-earning assets, but screener doesn't split those.
    if nii is not None and total_assets is not None and total_assets > 0:
        out["nim"] = round(nii / total_assets * 100, 2)

    # Non-interest income: only meaningful for banks where fees/treasury are
    # a real franchise axis. NBFCs book fee income inside the "revenue" row,
    # so screener's "other income" is essentially a rounding line (often
    # negligible or negative). Skip the ratio when it's structurally absent.
    if latest_other is not None and nii is not None and nii > 0:
        out["non_interest_income_cr"] = round(latest_other, 0)
        if (nii + latest_other) > 0 and abs(latest_other) / nii >= 0.02:
            out["non_interest_income_ratio"] = round(
                latest_other / (nii + latest_other) * 100, 2
            )

    # Credit-Deposit ratio ≈ (Total Assets − Investments − Fixed Assets) /
    # Deposits. Not surgical (includes some non-loan assets) but close
    # enough to flag the >100% CD ratio stress which is what matters.
    if (
        total_assets is not None
        and investments is not None
        and deposits is not None
        and deposits > 0
    ):
        net_loans = total_assets - investments
        if fixed_assets is not None:
            net_loans -= fixed_assets
        out["credit_deposit_ratio"] = round(net_loans / deposits * 100, 1)


def _apply_multi_year_trends(
    out: dict[str, Any],
    bs_headers: list[str],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
    r_rows: dict[str, list[float | None]],
) -> None:
    """Expose raw multi-year series so the report can show direction.

    Latest-only scoring misses the story: a ROE of 15% that was 25% three
    years ago signals degrading capital efficiency, which matters for the
    prospects judgment even if today's snapshot still looks healthy.
    """
    # ROCE % from the screener #ratios section (annual, oldest→newest).
    roce_series = r_rows.get("roce %")
    if roce_series:
        out["roce_annual_trend"] = [v for v in roce_series if v is not None]

    # Operating margin trend computed from P&L annual rows.
    sales = _drop_ttm(pl_headers, pl_rows.get("sales") or pl_rows.get("revenue"))
    op_profit = _drop_ttm(pl_headers, pl_rows.get("operating profit"))
    net_profit = _drop_ttm(pl_headers, pl_rows.get("net profit"))

    if sales:
        out["sales_annual_trend"] = [v for v in sales if v is not None]
    if net_profit:
        out["net_profit_annual_trend"] = [v for v in net_profit if v is not None]

    if sales and op_profit:
        # Walk the two series in lockstep and compute OP / Sales where both present.
        trend: list[float] = []
        for s, op in zip(sales, op_profit):
            if s is None or op is None or s <= 0:
                continue
            trend.append(round(op / s * 100, 2))
        if trend:
            out["operating_margin_annual_trend"] = trend

    # Borrowings trajectory — is debt expanding or shrinking?
    borrowings_series = (
        bs_rows.get("borrowings")
        or bs_rows.get("borrowing")
        or bs_rows.get("total debt")
    )
    if borrowings_series:
        clean = [v for v in borrowings_series if v is not None]
        if clean:
            out["borrowings_annual_trend"] = clean
            if len(clean) >= 4:
                latest = clean[-1]
                three_years_ago = clean[-4]
                if three_years_ago > 0:
                    out["borrowings_delta_3y_pct"] = round(
                        (latest - three_years_ago) / three_years_ago * 100, 1
                    )
                elif latest > 0:
                    # Went from zero debt to some debt — direction is positive.
                    out["borrowings_delta_3y_pct"] = float("inf")
                else:
                    out["borrowings_delta_3y_pct"] = 0.0


def _apply_shareholding_trend(tree, out: dict[str, Any]) -> None:
    """Parse the quarterly shareholding pattern table.

    Screener's `#shareholding > #quarterly-shp > table` has up to 12
    quarters of Promoter / FII / DII / Public / Government / Number-of-
    shareholders rows. The year-over-year deltas in these lines are the
    cleanest "smart money vs domestic money" tape we can get from the
    standard screener page — watch for FII exit + DII accumulation
    (classic re-rating warning) and promoter buying/selling (insider
    conviction signal).
    """
    sh_el = tree.css_first("#shareholding")
    if sh_el is None:
        return
    q_el = sh_el.css_first("#quarterly-shp")
    if q_el is None:
        return
    tbl = q_el.css_first("table")
    if tbl is None:
        return

    raw_headers = [th.text(strip=True) for th in tbl.css("thead th")]
    headers = [h for h in raw_headers if h]
    if not headers:
        return

    iso_dates: list[str] = []
    for h in headers:
        iso = _screener_month_to_iso(h)
        iso_dates.append(iso if iso else h)

    rows: dict[str, list[float | None]] = {}
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        raw_label = cells[0].text(strip=True)
        if not raw_label:
            continue
        label = raw_label.lower().rstrip("+").strip()
        values: list[float | None] = []
        for c in cells[1:]:
            text = c.text(strip=True)
            if not text or text == "--":
                values.append(None)
                continue
            # Shareholding cells are usually "N.NN%"; number of shareholders
            # is a raw comma-separated integer.
            parsed = _parse_pct(text)
            if parsed is None:
                parsed = _parse_float(text)
            values.append(parsed)
        if any(v is not None for v in values):
            rows[label] = values

    def _reverse_clean(series: list[float | None] | None) -> list[float]:
        """Oldest→newest with None gaps becomes newest-first, None-stripped."""
        if not series:
            return []
        return list(reversed([v for v in series if v is not None]))

    promoter = rows.get("promoters")
    fii = rows.get("fiis")
    dii = rows.get("diis")
    public = rows.get("public")
    n_sh = rows.get("no. of shareholders")

    # Dates aligned with the raw (oldest→newest) row; reverse to newest-first.
    if iso_dates:
        out["shareholding_dates"] = list(reversed(iso_dates))

    out["promoter_holding_trend"] = _reverse_clean(promoter)
    out["fii_holding_trend"] = _reverse_clean(fii)
    out["dii_holding_trend"] = _reverse_clean(dii)
    out["public_holding_trend"] = _reverse_clean(public)
    out["num_shareholders_trend"] = _reverse_clean(n_sh)

    # 1y deltas — most-recent minus 4-quarters-back. Values are in %
    # for promoter/FII/DII/public, so the delta is in percentage points.
    def _delta_1y(series: list[float]) -> float | None:
        if len(series) < 5:
            return None
        return round(series[0] - series[4], 2)

    def _delta_1y_pct(series: list[float]) -> float | None:
        if len(series) < 5 or series[4] == 0:
            return None
        return round((series[0] - series[4]) / series[4] * 100, 1)

    out["fii_delta_1y_pct_points"] = _delta_1y(out["fii_holding_trend"])
    out["dii_delta_1y_pct_points"] = _delta_1y(out["dii_holding_trend"])
    out["promoter_delta_1y_pct_points"] = _delta_1y(out["promoter_holding_trend"])
    out["num_shareholders_delta_1y_pct"] = _delta_1y_pct(
        out["num_shareholders_trend"]
    )


def _latest(values: list[float | None] | None) -> float | None:
    if not values:
        return None
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _screener_cagr(
    values: list[float | None] | None, *, years: int
) -> float | None:
    """CAGR from an annual row (TTM already dropped by caller).

    Requires `years + 1` populated points. Picks the last and `years`-th-
    last populated points to avoid crashing on missing middle cells. If
    either endpoint is missing or non-positive, returns None.
    """
    if not values or len(values) < years + 1:
        return None
    current = values[-1]
    past = values[-(years + 1)]
    if current is None or past is None:
        return None
    if past <= 0 or current <= 0:
        return None
    return round((((current / past) ** (1 / years)) - 1) * 100, 2)


_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _screener_month_to_iso(label: str) -> str | None:
    """Convert e.g. 'Sep 2025' → '2025-09-30', 'Mar 2025' → '2025-03-31'."""
    if not label:
        return None
    parts = label.strip().split()
    if len(parts) != 2:
        return None
    mon_key = parts[0][:3].lower()
    if mon_key not in _MONTH_MAP:
        return None
    try:
        year = int(parts[1])
    except ValueError:
        return None
    month = _MONTH_MAP[mon_key]
    # Approximate end-of-month day by month.
    end_day = {"01": "31", "02": "28", "03": "31", "04": "30",
               "05": "31", "06": "30", "07": "31", "08": "31",
               "09": "30", "10": "31", "11": "30", "12": "31"}[month]
    return f"{year:04d}-{month}-{end_day}"


def _parse_pct(text: str) -> float | None:
    if text is None:
        return None
    try:
        cleaned = text.strip().replace(",", "").rstrip("%")
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def _parse_float(text: str) -> float | None:
    if text is None:
        return None
    try:
        return float(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


# ── Merge logic ───────────────────────────────────────────────────────────────


def _merge(
    yf_data: dict[str, Any], screener_data: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(yf_data)
    for key, value in screener_data.items():
        if value is None:
            continue
        if key in _SCREENER_WINS or key not in merged or merged[key] in (None, ""):
            merged[key] = value
    return merged


def _source_tag(yf_data: dict[str, Any], screener_data: dict[str, Any]) -> str:
    yf_ok = bool(yf_data)
    sc_ok = bool(screener_data)
    if yf_ok and sc_ok:
        return "yfinance+screener"
    if yf_ok:
        return "yfinance"
    if sc_ok:
        return "screener"
    return "empty"


def _field_sources(
    yf_data: dict[str, Any], screener_data: dict[str, Any]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in SCORED_FIELDS:
        if f in screener_data and (f in _SCREENER_WINS or f not in yf_data):
            out[f] = "screener"
        elif f in yf_data:
            out[f] = "yfinance"
    return out
