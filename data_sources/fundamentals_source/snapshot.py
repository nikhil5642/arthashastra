"""`BalanceSheetSnapshot` — the canonical output shape of the fundamentals
fetch + the sector-detection helper (`is_bank_snapshot`) that routes a
snapshot to the industrial or bank rubric downstream.

Everything in this module is pure data: no I/O, no parsing, no fetching.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

# ── Scored fields (feed the data_completeness_pct metric) ────────────────────

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


# ── Sector routing ───────────────────────────────────────────────────────────

# Primary signal is `snap.is_bank` (set by the screener parser when the P&L
# has the bank row shape). These strings are the fallback for snapshots where
# that flag didn't fire — e.g. yfinance-only fetches or when screener failed.
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


# ── The snapshot itself ──────────────────────────────────────────────────────


@dataclass(slots=True)
class BalanceSheetSnapshot:
    ticker: str
    as_of_date: str

    # ── Scored: leverage / liquidity ─────────────────────────────────────────
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    net_debt_to_ebitda: float | None = None

    # ── Scored: profitability ────────────────────────────────────────────────
    roe: float | None = None
    roce: float | None = None
    roa: float | None = None

    # ── Scored: growth ───────────────────────────────────────────────────────
    rev_3y_cagr: float | None = None
    pat_3y_cagr: float | None = None

    # ── Scored: earnings quality ─────────────────────────────────────────────
    ocf_to_pat: float | None = None
    fcf_to_pat: float | None = None

    # ── Scored: promoter ─────────────────────────────────────────────────────
    promoter_holding: float | None = None
    pledge_pct: float | None = None

    # ── Price context ────────────────────────────────────────────────────────
    current_price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap_cr: float | None = None
    pe_ratio: float | None = None
    book_value_per_share: float | None = None
    dividend_yield: float | None = None

    # ── Financial profile: margins ───────────────────────────────────────────
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None

    # ── Financial profile: cost structure ────────────────────────────────────
    cogs_to_revenue: float | None = None
    sga_to_revenue: float | None = None
    tax_rate: float | None = None

    # ── Financial profile: working capital days ──────────────────────────────
    debtor_days: float | None = None
    inventory_days: float | None = None
    payable_days: float | None = None
    cash_conversion_cycle_days: float | None = None

    # ── Financial profile: asset composition ─────────────────────────────────
    tangible_book_ratio: float | None = None  # tangible BV / equity
    goodwill_to_equity: float | None = None  # intangibles / equity

    # ── Bank-specific metrics (populated only for banks / NBFCs) ─────────────
    # NIM = (Interest Income − Interest Expense) / Total Assets. Closer to
    # reality than (NII / average interest-earning assets) but uses only what
    # screener exposes so no cross-period mismatch.
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
    # 1-year deltas (most-recent − 4 quarters back).
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
    # Downstream code can ask any question about any line item without the
    # parser having to learn new labels first.
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
    raw_shareholding_quarterly: dict[str, list[float | None]] = field(
        default_factory=dict
    )
    raw_shareholding_headers: list[str] = field(default_factory=list)

    # ── Peer comparison and documents (from screener) ────────────────────────
    peers: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, str]] = field(default_factory=list)
    screener_analysis_text: str = ""

    # ── yfinance extras (analyst bundle, metadata — best-effort) ─────────────
    analyst_target_mean: float | None = None
    analyst_target_high: float | None = None
    analyst_target_low: float | None = None
    # 1 = Strong Buy, 2 = Buy, 3 = Hold, 4 = Sell, 5 = Strong Sell.
    analyst_recommendation_mean: float | None = None
    analyst_opinion_count: int | None = None
    country: str = ""
    website: str = ""
    full_time_employees: int | None = None
    business_summary: str = ""  # yfinance longBusinessSummary

    # ── Financial profile: quarterly trend ───────────────────────────────────
    quarterly_revenue: list[float] = field(default_factory=list)  # newest-first
    quarterly_pat: list[float] = field(default_factory=list)
    quarterly_dates: list[str] = field(default_factory=list)
    latest_yoy_revenue_growth: float | None = None
    latest_yoy_pat_growth: float | None = None

    # ── Metadata ─────────────────────────────────────────────────────────────
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
