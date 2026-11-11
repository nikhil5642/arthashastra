"""`BalanceSheetSnapshot` — the canonical output shape of the fundamentals
fetch + the rubric router (`is_bank_snapshot`) that sends a snapshot to
the industrial or bank rubric downstream, + `DataConflict`, the record of
two sources disagreeing about one field.

Everything in this module is pure data: no I/O, no parsing, no fetching.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from typing import TypedDict

from data_sources.fx_source import major_unit

# Public row aliases for the nested JSON blobs the snapshot carries.
# Both are deliberately loose — screener's peer table and documents
# list have variable columns depending on the sector and the company's
# listing history — so callers get a typed "this is a stringly-keyed
# row" shape without pretending to know every possible column.
PeerRecord = dict[str, float | str]
DocumentRecord = dict[str, str]

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
    "insider_holding",
    "pledge_pct",
)


class StatementWindow(StrEnum):
    """Which twelve months a flow figure (profit, cash flow) covers."""

    TTM = "TTM"  # the four newest consecutive quarters
    FISCAL_YEAR = "FY"  # the latest full fiscal year

    @property
    def label(self) -> str:
        return "trailing twelve months" if self is StatementWindow.TTM else "latest fiscal year"


# ── Rubric routing (structure first, sector strings second) ──────────────────

# Sector / industry strings that mark a company as *some kind* of financial.
# On their own they are not enough to pick the bank rubric — see below.
_FINANCIAL_SECTOR_KEYWORDS: tuple[str, ...] = (
    "bank",
    "nbfc",
    "insurance",
    "asset management",
    "financial services",
    "capital markets",
    "credit services",
    "mortgage",
)


def is_bank_snapshot(snap: "BalanceSheetSnapshot") -> bool:
    """Should this snapshot be scored on the bank rubric?

    Shared by the scorer, CLI, and report layers.

    1. `snap.is_bank` — screener saw the bank / NBFC P&L shape (interest
       income, interest expense, financing profit or deposits). Authoritative.
    2. A financial sector string alone is NOT enough. Payment networks,
       exchanges, asset managers, brokers and rating agencies all carry
       Yahoo's "Financial Services" sector but run ordinary asset-light
       P&Ls and publish balance sheets with a current-assets /
       current-liabilities split — so a populated `current_ratio` means
       "score on the industrial rubric".
    3. Financial sector + no current-ratio split (Yahoo's bank / insurer
       statement template, or no yfinance data at all) → bank rubric.
    """
    if snap.is_bank:
        return True
    haystack = f"{snap.sector or ''} {snap.industry or ''}".lower()
    if not any(kw in haystack for kw in _FINANCIAL_SECTOR_KEYWORDS):
        return False
    return snap.current_ratio is None


# ── Source disagreement record ───────────────────────────────────────────────


@dataclass(slots=True)
class DataConflict:
    """Two adapters reported materially different values for one field.

    The merge still picks `chosen_source` by the normal precedence rules;
    this record exists so the disagreement is visible in the report
    instead of being resolved silently.
    """

    field_name: str
    chosen_source: str
    chosen_value: float
    other_source: str
    other_value: float
    rel_diff_pct: float

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "DataConflict | None":
        try:
            return cls(
                field_name=str(data["field_name"]),
                chosen_source=str(data["chosen_source"]),
                chosen_value=float(data["chosen_value"]),  # type: ignore[arg-type]
                other_source=str(data["other_source"]),
                other_value=float(data["other_value"]),  # type: ignore[arg-type]
                rel_diff_pct=float(data["rel_diff_pct"]),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            return None


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

    # ── Scored: ownership ────────────────────────────────────────────────────
    # % held by insiders: the promoter group for Indian listings (screener),
    # officers / founders / controlling parents elsewhere (Yahoo).
    insider_holding: float | None = None
    pledge_pct: float | None = None

    # ── Listing identity ─────────────────────────────────────────────────────
    exchange: str = ""  # e.g. "NasdaqGS", "NSE", "HKSE"
    currency: str = ""  # trading currency: prices, market cap, targets
    financial_currency: str = ""  # reporting currency: every statement amount

    # ── Price context (trading currency) ─────────────────────────────────────
    current_price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap: float | None = None  # absolute amount, not crores / millions
    pe_ratio: float | None = None
    book_value_per_share: float | None = None
    dividend_yield: float | None = None

    # ── Valuation inputs (reporting currency) ───────────────────────────────
    eps_history: dict[str, float] = field(default_factory=dict)  # FY end (ISO) → diluted EPS
    net_income: float | None = None  # latest full fiscal year, absolute
    free_cash_flow: float | None = None  # latest full fiscal year, absolute
    shareholders_equity: float | None = None  # latest balance sheet, absolute

    # ── Latest fiscal year, absolute amounts (reporting currency) ────────────
    revenue: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None  # cash spent on property and equipment, as a positive number
    stock_based_compensation: float | None = None

    # ── Trailing twelve months: the four newest consecutive quarters ─────────
    # All None when the source publishes fewer than four consecutive quarters
    # (half-yearly reporters); consumers then fall back to the fiscal year.
    ttm_period_end: str = ""  # ISO date of the newest quarter in the window
    ttm_revenue: float | None = None
    ttm_net_income: float | None = None
    ttm_operating_cash_flow: float | None = None
    ttm_capex: float | None = None
    ttm_free_cash_flow: float | None = None
    ttm_stock_based_compensation: float | None = None

    # ── Earnings quality ─────────────────────────────────────────────────────
    # Profit excluding unusual items (investment gains, write-offs, one-off
    # charges) ÷ reported profit, both from the same source and window.
    # 0.56 means 44% of reported profit was unusual. None when the source
    # publishes no normalized income or the company made a loss.
    core_earnings_ratio_fy: float | None = None
    core_earnings_ratio_ttm: float | None = None

    # ── Valuation ratios (set by valuation.apply_valuation) ──────────────────
    earnings_yield: float | None = None  # %, 100 / P/E
    fcf_yield: float | None = None  # %, free cash flow / market value of the equity
    price_to_book: float | None = None
    pe_history: list[float] = field(default_factory=list)  # P/E at past FY ends, oldest first
    earnings_window: StatementWindow | None = None  # the twelve months behind `pe_ratio`
    cash_flow_window: StatementWindow | None = None  # the twelve months behind `fcf_yield`
    # The quote's price and the statements can't be put on one basis: a
    # depositary receipt priced per fraction of a share (a CEDEAR, an SDR), or
    # statements whose currency label is wrong. Nothing price-based is kept.
    quote_unreliable: bool = False

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
    # screener exposes so no cross-period mismatch. Amounts are absolute, in
    # the reporting currency.
    nim: float | None = None  # %
    net_interest_income: float | None = None  # latest full fiscal year
    non_interest_income: float | None = None  # fee + treasury
    non_interest_income_ratio: float | None = None  # % of total income
    deposits: float | None = None
    borrowings: float | None = None
    credit_deposit_ratio: float | None = None
    is_bank: bool = False
    # Asset quality — latest quarter, from screener's bank quarterly table.
    gross_npa_pct: float | None = None  # % of gross advances
    net_npa_pct: float | None = None  # % of net advances
    gross_npa_delta_1y_pct_points: float | None = None  # + = deteriorating
    net_npa_delta_1y_pct_points: float | None = None

    # ── Shareholding trend (quarterly, newest-first) ─────────────────────────
    # Captures the smart-money / domestic-money tug of war that often
    # explains why a name is trending up or down before the fundamentals
    # catch up. All series are aligned with `shareholding_dates`.
    shareholding_dates: list[str] = field(default_factory=list)
    insider_holding_trend: list[float] = field(default_factory=list)  # %
    institutional_holding: float | None = None  # latest %, all institutions
    fii_holding_trend: list[float] = field(default_factory=list)  # %
    dii_holding_trend: list[float] = field(default_factory=list)  # %
    public_holding_trend: list[float] = field(default_factory=list)  # %
    num_shareholders_trend: list[float] = field(default_factory=list)  # raw count
    # 1-year deltas (most-recent − 4 quarters back).
    fii_delta_1y_pct_points: float | None = None
    dii_delta_1y_pct_points: float | None = None
    insider_delta_1y_pct_points: float | None = None
    num_shareholders_delta_1y_pct: float | None = None

    # ── Year-on-year balance-sheet changes ───────────────────────────────────
    # The latest balance sheet (quarterly or half-yearly when published)
    # against the one about twelve months earlier — the changes annual
    # ratios only show a year late. All currency-free.
    balance_sheet_date: str = ""  # ISO date of the latest balance sheet compared
    debt_change_1y_pct: float | None = None
    latest_debt_to_equity: float | None = None
    debt_to_equity_1y_ago: float | None = None
    receivables_change_1y_pct: float | None = None
    inventory_change_1y_pct: float | None = None
    goodwill_change_1y_pct: float | None = None  # goodwill + other intangibles
    latest_goodwill_to_equity: float | None = None
    # Shares outstanding; None across a stock split (the count isn't comparable).
    share_count_change_1y_pct: float | None = None

    # ── Multi-year ratio / P&L trend (oldest→newest, annual) ─────────────────
    # Raw series so the report can render direction (↑/↓/→) and Claude can
    # reason about "is the business improving or degrading?" without just
    # looking at the latest snapshot. Amounts are absolute, in the reporting
    # currency; margins and ROCE are in percent.
    roce_annual_trend: list[float] = field(default_factory=list)
    operating_margin_annual_trend: list[float] = field(default_factory=list)
    net_margin_annual_trend: list[float] = field(default_factory=list)
    net_profit_annual_trend: list[float] = field(default_factory=list)
    sales_annual_trend: list[float] = field(default_factory=list)
    borrowings_annual_trend: list[float] = field(default_factory=list)
    borrowings_delta_3y_pct: float | None = None  # + = debt grew, − = deleveraged
    # Debt appeared from a zero base, so a % change is undefined. Replaces
    # the old float('inf') sentinel, which isn't valid JSON.
    borrowings_went_from_zero: bool = False

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
    peers: list[PeerRecord] = field(default_factory=list)
    documents: list[DocumentRecord] = field(default_factory=list)
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
    # Operating profit ÷ revenue per quarter, %, aligned with `quarterly_dates`
    # (None where the source has no operating line for that quarter).
    quarterly_operating_margin: list[float | None] = field(default_factory=list)
    latest_yoy_revenue_growth: float | None = None
    latest_yoy_pat_growth: float | None = None

    # ── Metadata ─────────────────────────────────────────────────────────────
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    source: str = "unknown"
    # ISO date of the latest full fiscal year behind the annual ratios.
    latest_annual_period: str = ""
    # Every populated field → the adapter whose value the merge kept.
    field_sources: dict[str, str] = field(default_factory=dict)
    data_conflicts: list[DataConflict] = field(default_factory=list)

    @property
    def market_cap_currency(self) -> str:
        """Market cap is in the major unit even for pence-quoted listings."""
        return major_unit(self.currency)[0]

    @property
    def data_completeness_pct(self) -> float:
        present = sum(1 for f in SCORED_FIELDS if getattr(self, f) is not None)
        return round(100.0 * present / len(SCORED_FIELDS), 1)

    @property
    def ttm_is_current(self) -> bool:
        """A trailing-twelve-month window exists and is no older than the fiscal year."""
        return bool(self.ttm_period_end) and self.ttm_period_end >= self.latest_annual_period

    @property
    def ttm_adds_news(self) -> bool:
        """The trailing window ends after the fiscal year — it holds quarters the annual ratios lack."""
        return bool(self.ttm_period_end) and self.ttm_period_end > self.latest_annual_period

    @property
    def core_earnings_ratio(self) -> float | None:
        """Core ÷ reported profit over the twelve months the P/E covers."""
        if self.earnings_window is StatementWindow.FISCAL_YEAR:
            return self.core_earnings_ratio_fy
        if self.core_earnings_ratio_ttm is not None and self.ttm_is_current:
            return self.core_earnings_ratio_ttm
        return self.core_earnings_ratio_fy

    @property
    def trailing_net_margin(self) -> float | None:
        """Net margin (%) over the freshest twelve months available."""
        if (
            self.earnings_window is not StatementWindow.FISCAL_YEAR
            and self.ttm_is_current
            and self.ttm_net_income is not None
            and self.ttm_revenue
            and self.ttm_revenue > 0
        ):
            return round(self.ttm_net_income / self.ttm_revenue * 100, 2)
        return self.net_margin

    def trailing(self, name: str) -> tuple[float | None, StatementWindow]:
        """The freshest twelve-month figure for a flow line, and which window it covers.

        `name` is the fiscal-year field (`"free_cash_flow"`, `"capex"`, …);
        its `ttm_` twin wins when a current trailing window has it.
        """
        if self.ttm_is_current:
            ttm_value = getattr(self, f"ttm_{name}")
            if ttm_value is not None:
                return ttm_value, StatementWindow.TTM
        return getattr(self, name), StatementWindow.FISCAL_YEAR

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BalanceSheetSnapshot":
        allowed = set(cls.__dataclass_fields__)
        kwargs: dict[str, object] = {
            k: v for k, v in data.items() if k in allowed
        }
        kwargs.setdefault("ticker", "")
        kwargs.setdefault("as_of_date", date.today().isoformat())
        raw_conflicts = kwargs.get("data_conflicts")
        conflicts: list[DataConflict] = []
        if isinstance(raw_conflicts, list):
            for row in raw_conflicts:
                parsed = (
                    row
                    if isinstance(row, DataConflict)
                    else DataConflict.from_dict(row) if isinstance(row, dict) else None
                )
                if parsed is not None:
                    conflicts.append(parsed)
        kwargs["data_conflicts"] = conflicts
        for window_field in ("earnings_window", "cash_flow_window"):
            kwargs[window_field] = _parse_window(kwargs.get(window_field))
        return cls(**kwargs)  # type: ignore[arg-type]


def _parse_window(raw: object) -> StatementWindow | None:
    """A cached window label back into the enum; anything unknown is None."""
    if isinstance(raw, StatementWindow):
        return raw
    try:
        return StatementWindow(str(raw))
    except ValueError:
        return None


# ── SnapshotFields: the adapter-layer builder shape ────────────────────────
#
# Adapters (`yfinance_adapter`, `screener_adapter`) build up a partial view
# of a future `BalanceSheetSnapshot` field by field, then hand it to
# `source._merge()` which produces the final dataclass. Using a `TypedDict`
# here instead of `dict[str, Any]` gives type-checkers the list of valid
# keys (matching the dataclass field names), so a typo like
# `out["debt_to_equityy"] = 0.5` is caught at edit time rather than
# silently lost in the merge step.
#
# `total=False` marks every key as optional — any individual adapter call
# only fills the subset of fields it can compute from what the remote
# service returned. The shape is intentionally a near-mirror of
# `BalanceSheetSnapshot`; keep the two in sync when adding a new scored
# or profile field.


class SnapshotFields(TypedDict, total=False):
    """Partial, under-construction view of a `BalanceSheetSnapshot`.

    See the module-level note above. This is the builder type used by
    the yfinance and screener adapters.
    """

    ticker: str
    as_of_date: str

    # Scored: leverage / liquidity
    debt_to_equity: float | None
    interest_coverage: float | None
    current_ratio: float | None
    quick_ratio: float | None
    net_debt_to_ebitda: float | None

    # Scored: profitability
    roe: float | None
    roce: float | None
    roa: float | None

    # Scored: growth
    rev_3y_cagr: float | None
    pat_3y_cagr: float | None

    # Scored: earnings quality
    ocf_to_pat: float | None
    fcf_to_pat: float | None

    # Scored: ownership
    insider_holding: float | None
    pledge_pct: float | None

    # Listing identity
    exchange: str
    currency: str
    financial_currency: str

    # Price context
    current_price: float | None
    high_52w: float | None
    low_52w: float | None
    market_cap: float | None
    pe_ratio: float | None
    book_value_per_share: float | None
    dividend_yield: float | None

    # Valuation inputs + ratios
    eps_history: dict[str, float]
    net_income: float | None
    free_cash_flow: float | None
    shareholders_equity: float | None
    revenue: float | None
    operating_cash_flow: float | None
    capex: float | None
    stock_based_compensation: float | None
    ttm_period_end: str
    ttm_revenue: float | None
    ttm_net_income: float | None
    ttm_operating_cash_flow: float | None
    ttm_capex: float | None
    ttm_free_cash_flow: float | None
    ttm_stock_based_compensation: float | None
    core_earnings_ratio_fy: float | None
    core_earnings_ratio_ttm: float | None
    earnings_yield: float | None
    fcf_yield: float | None
    price_to_book: float | None
    pe_history: list[float]

    # Financial profile: margins
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None

    # Financial profile: cost structure
    cogs_to_revenue: float | None
    sga_to_revenue: float | None
    tax_rate: float | None

    # Financial profile: working capital days
    debtor_days: float | None
    inventory_days: float | None
    payable_days: float | None
    cash_conversion_cycle_days: float | None

    # Financial profile: asset composition
    tangible_book_ratio: float | None
    goodwill_to_equity: float | None

    # Bank-specific metrics
    nim: float | None
    net_interest_income: float | None
    non_interest_income: float | None
    non_interest_income_ratio: float | None
    deposits: float | None
    borrowings: float | None
    credit_deposit_ratio: float | None
    is_bank: bool
    gross_npa_pct: float | None
    net_npa_pct: float | None
    gross_npa_delta_1y_pct_points: float | None
    net_npa_delta_1y_pct_points: float | None

    # Shareholding trend
    shareholding_dates: list[str]
    insider_holding_trend: list[float]
    institutional_holding: float | None
    fii_holding_trend: list[float]
    dii_holding_trend: list[float]
    public_holding_trend: list[float]
    num_shareholders_trend: list[float]
    fii_delta_1y_pct_points: float | None
    dii_delta_1y_pct_points: float | None
    insider_delta_1y_pct_points: float | None
    num_shareholders_delta_1y_pct: float | None

    # Year-on-year balance-sheet changes
    balance_sheet_date: str
    debt_change_1y_pct: float | None
    latest_debt_to_equity: float | None
    debt_to_equity_1y_ago: float | None
    receivables_change_1y_pct: float | None
    inventory_change_1y_pct: float | None
    goodwill_change_1y_pct: float | None
    latest_goodwill_to_equity: float | None
    share_count_change_1y_pct: float | None

    # Multi-year ratio / P&L trend
    roce_annual_trend: list[float]
    operating_margin_annual_trend: list[float]
    net_margin_annual_trend: list[float]
    net_profit_annual_trend: list[float]
    sales_annual_trend: list[float]
    borrowings_annual_trend: list[float]
    borrowings_delta_3y_pct: float | None
    borrowings_went_from_zero: bool

    # Full raw screener dump
    raw_profit_loss: dict[str, list[float | None]]
    raw_profit_loss_headers: list[str]
    raw_balance_sheet: dict[str, list[float | None]]
    raw_balance_sheet_headers: list[str]
    raw_cash_flow: dict[str, list[float | None]]
    raw_cash_flow_headers: list[str]
    raw_ratios: dict[str, list[float | None]]
    raw_ratios_headers: list[str]
    raw_quarters: dict[str, list[float | None]]
    raw_quarters_headers: list[str]
    raw_shareholding_quarterly: dict[str, list[float | None]]
    raw_shareholding_headers: list[str]

    # Peer comparison and documents
    peers: list[PeerRecord]
    documents: list[DocumentRecord]
    screener_analysis_text: str

    # yfinance extras (analyst bundle, metadata)
    analyst_target_mean: float | None
    analyst_target_high: float | None
    analyst_target_low: float | None
    analyst_recommendation_mean: float | None
    analyst_opinion_count: int | None
    country: str
    website: str
    full_time_employees: int | None
    business_summary: str

    # Quarterly trend
    quarterly_revenue: list[float]
    quarterly_pat: list[float]
    quarterly_dates: list[str]
    quarterly_operating_margin: list[float | None]
    latest_yoy_revenue_growth: float | None
    latest_yoy_pat_growth: float | None

    # Metadata
    company_name: str
    sector: str
    industry: str
    source: str
    latest_annual_period: str
    field_sources: dict[str, str]
