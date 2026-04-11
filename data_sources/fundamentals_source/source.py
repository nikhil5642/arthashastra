"""`FundamentalsSource` — the async facade over yfinance + screener.in.

This is the only public entry point callers should use to build a
`BalanceSheetSnapshot`. It fans out to both adapters in parallel,
merges their outputs, and constructs the final snapshot.

Merge rules:
  - For any field in `_SCREENER_WINS`, screener wins if it emitted a
    value (even over a populated yfinance value). This is because
    screener's definitions are the canonical ones for Indian equities:
    "Borrowings" (not lease-inflated "Total Debt"), "Operating Profit"
    (not yfinance's ebit variants), etc.
  - For everything else, yfinance fills gaps: any field screener didn't
    provide falls back to yfinance's value.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

# Import the adapter modules (not the functions) so monkeypatching at
# `yfinance_adapter._fetch_yfinance_sync` / `screener_adapter._fetch_screener_in`
# is picked up here at call time — the attribute lookup is deferred.
from data_sources.fundamentals_source import screener_adapter, yfinance_adapter
from data_sources.fundamentals_source.snapshot import (
    BalanceSheetSnapshot,
    SCORED_FIELDS,
    SnapshotFields,
)

log = logging.getLogger(__name__)

PriceRow = tuple[str, float]  # (ISO date, adjusted close)


# ── Fields where screener.in is authoritative ───────────────────────────────
#
# yfinance's "Total Debt" row includes operating-lease liabilities and
# other interest-bearing items that Indian analysts exclude; screener's
# "Borrowings" row matches what Indian investors actually use. Same story
# for OCF / FCF / working capital days / ROE / ROCE — screener's
# definitions are the canonical ones for Indian equities.

_SCREENER_WINS: frozenset[str] = frozenset({
    # Per-field ratios screener pre-computes
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
    # yfinance occasionally drops a middle quarter for recent IPOs —
    # screener's per-company quarters table is more complete for India.
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
    # Shareholding trend (12 quarters of FII / DII / promoter / public).
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
    # Full raw dumps — always from screener.
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


# ── Result + facade ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class FetchResult:
    """Everything a single yfinance + screener fetch produces."""

    snapshot: BalanceSheetSnapshot
    price_history: list[PriceRow] = field(default_factory=list)


class FundamentalsSource:
    """Async facade over live yfinance + screener.in.

    Usage::

        source = FundamentalsSource()
        snapshot = await source.fetch("RELIANCE")               # snapshot only
        result = await source.fetch_full("RELIANCE")            # + price history
    """

    async def fetch(self, ticker: str) -> BalanceSheetSnapshot:
        result = await self.fetch_full(ticker)
        return result.snapshot

    async def fetch_full(self, ticker: str) -> FetchResult:
        bare = ticker.strip().upper()
        log.info("fundamentals: live fetch for %s", bare)

        yf_task = yfinance_adapter._fetch_yfinance(bare)
        screener_task = screener_adapter._fetch_screener_in(bare)
        yf_result, screener_result = await asyncio.gather(
            yf_task, screener_task, return_exceptions=True
        )

        yf_data: SnapshotFields
        price_history: list[PriceRow]
        if isinstance(yf_result, Exception):
            log.warning("yfinance failed for %s: %s", bare, yf_result)
            yf_data, price_history = SnapshotFields(), []
        else:
            yf_data, price_history = yf_result

        screener_data: SnapshotFields
        if isinstance(screener_result, Exception):
            log.warning("screener.in failed for %s: %s", bare, screener_result)
            screener_data = SnapshotFields()
        else:
            screener_data = screener_result

        merged = _merge(yf_data, screener_data)
        merged["ticker"] = bare
        merged["as_of_date"] = date.today().isoformat()
        merged["source"] = _source_tag(yf_data, screener_data)

        allowed = set(BalanceSheetSnapshot.__dataclass_fields__)
        kwargs = {k: v for k, v in merged.items() if k in allowed}
        snap = BalanceSheetSnapshot(**kwargs)
        snap.field_sources = _field_sources(yf_data, screener_data)
        return FetchResult(snapshot=snap, price_history=price_history)


# ── Merge helpers ────────────────────────────────────────────────────────────


def _merge(
    yf_data: SnapshotFields, screener_data: SnapshotFields
) -> SnapshotFields:
    """Combine yfinance + screener per `_SCREENER_WINS` precedence."""
    merged: SnapshotFields = dict(yf_data)  # type: ignore[assignment]
    for key, value in screener_data.items():
        if value is None:
            continue
        if (
            key in _SCREENER_WINS
            or key not in merged
            or merged[key] in (None, "")  # type: ignore[literal-required]
        ):
            merged[key] = value  # type: ignore[literal-required]
    return merged


def _source_tag(
    yf_data: SnapshotFields, screener_data: SnapshotFields
) -> str:
    """Annotate `snap.source` so the reader can see which adapters fired."""
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
    yf_data: SnapshotFields, screener_data: SnapshotFields
) -> dict[str, str]:
    """Attribute each SCORED field to whichever adapter provided it."""
    out: dict[str, str] = {}
    for f in SCORED_FIELDS:
        if f in screener_data and (f in _SCREENER_WINS or f not in yf_data):
            out[f] = "screener"
        elif f in yf_data:
            out[f] = "yfinance"
    return out
