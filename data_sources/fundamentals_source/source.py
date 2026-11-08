"""`FundamentalsSource` — the async facade over yfinance + screener.in.

This is the only public entry point callers should use to build a
`BalanceSheetSnapshot`. It fans out to both adapters in parallel,
merges their outputs, and constructs the final snapshot.

Merge rules:
  - Non-finite floats (NaN / inf) are dropped from both inputs before
    anything else looks at them — a NaN that reaches a scorer slips past
    every `<` / `>=` comparison.
  - For any field in `_SCREENER_WINS`, screener wins if it emitted a
    value (even over a populated yfinance value). This is because
    screener's definitions are the canonical ones for Indian equities:
    "Borrowings" (not lease-inflated "Total Debt"), etc.
  - For everything else, yfinance fills gaps: any field screener didn't
    provide falls back to yfinance's value.

Provenance: `snap.field_sources` names the adapter behind every populated
field, and `snap.data_conflicts` records each scored field where both
adapters answered but disagreed materially — the merge still picks one,
but the disagreement is surfaced instead of being resolved silently.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date

# Import the adapter modules (not the functions) so monkeypatching at
# `yfinance_adapter._fetch_yfinance_sync` / `screener_adapter._fetch_screener_in`
# is picked up here at call time — the attribute lookup is deferred.
from data_sources.fundamentals_source import screener_adapter, yfinance_adapter
from data_sources.fundamentals_source.snapshot import (
    BalanceSheetSnapshot,
    DataConflict,
    SCORED_FIELDS,
    SnapshotFields,
)
from data_sources.fundamentals_source.valuation import apply_valuation
from data_sources.price_series import PriceSeries

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
    "insider_holding",
    "pledge_pct",
    "market_cap",
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
    # Valuation inputs from the statements screener authoritatively parses.
    "eps_history",
    "net_income",
    "free_cash_flow",
    "shareholders_equity",
    # yfinance occasionally drops a middle quarter for recent IPOs —
    # screener's per-company quarters table is more complete for India.
    "quarterly_revenue",
    "quarterly_pat",
    "quarterly_dates",
    "latest_yoy_revenue_growth",
    "latest_yoy_pat_growth",
    # Bank-specific metrics — always from screener's P&L / BS structure.
    "nim",
    "net_interest_income",
    "non_interest_income",
    "non_interest_income_ratio",
    "deposits",
    "borrowings",
    "credit_deposit_ratio",
    "is_bank",
    "gross_npa_pct",
    "net_npa_pct",
    "gross_npa_delta_1y_pct_points",
    "net_npa_delta_1y_pct_points",
    # Shareholding trend (12 quarters of FII / DII / promoter / public).
    "shareholding_dates",
    "insider_holding_trend",
    "fii_holding_trend",
    "dii_holding_trend",
    "public_holding_trend",
    "num_shareholders_trend",
    "fii_delta_1y_pct_points",
    "dii_delta_1y_pct_points",
    "insider_delta_1y_pct_points",
    "num_shareholders_delta_1y_pct",
    # Multi-year ratio / P&L trend series.
    "roce_annual_trend",
    "operating_margin_annual_trend",
    "net_profit_annual_trend",
    "sales_annual_trend",
    "borrowings_annual_trend",
    "borrowings_delta_3y_pct",
    "borrowings_went_from_zero",
    # Period of the annual statements behind the ratios screener supplies.
    "latest_annual_period",
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


# ── Conflict detection thresholds ────────────────────────────────────────────
#
# A conflict needs BOTH a relative gap (≥ 25%) and an absolute gap above a
# per-field floor, so two tiny numbers (D/E 0.02 vs 0.03) or two huge ones
# (interest cover 80× vs 110×) don't drown the report in noise.

_CONFLICT_REL_THRESHOLD = 0.25

_CONFLICT_ABS_FLOOR: dict[str, float] = {
    "debt_to_equity": 0.10,
    "interest_coverage": 1.0,
    "current_ratio": 0.15,
    "quick_ratio": 0.15,
    "net_debt_to_ebitda": 0.30,
    "roe": 2.0,
    "roce": 2.0,
    "roa": 1.0,
    "rev_3y_cagr": 2.0,
    "pat_3y_cagr": 2.0,
    "ocf_to_pat": 0.15,
    "fcf_to_pat": 0.15,
    "insider_holding": 1.0,
    "pledge_pct": 1.0,
}


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

        yf_data = _drop_non_finite(yf_data, label="yfinance")
        screener_data = _drop_non_finite(screener_data, label="screener")

        merged = _merge(yf_data, screener_data)
        merged["ticker"] = bare
        merged["as_of_date"] = date.today().isoformat()
        merged["source"] = _source_tag(yf_data, screener_data)

        allowed = set(BalanceSheetSnapshot.__dataclass_fields__)
        kwargs = {k: v for k, v in merged.items() if k in allowed}
        snap = BalanceSheetSnapshot(**kwargs)
        snap.field_sources = _field_sources(yf_data, screener_data)
        snap.data_conflicts = _detect_conflicts(yf_data, screener_data)
        apply_valuation(snap, PriceSeries.from_rows(price_history))
        return FetchResult(snapshot=snap, price_history=price_history)


# ── Merge helpers ────────────────────────────────────────────────────────────


def _drop_non_finite(data: SnapshotFields, *, label: str) -> SnapshotFields:
    """Remove NaN / inf scalars so they can never reach a scorer."""
    clean: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, float) and not math.isfinite(value):
            log.debug("%s: dropped non-finite %s=%r", label, key, value)
            continue
        clean[key] = value
    return clean  # type: ignore[return-value]


def _screener_preferred(
    key: str, screener_value: object, yf_value: object
) -> bool:
    """The single precedence rule shared by `_merge` and `_field_sources`."""
    if screener_value is None:
        return False
    return key in _SCREENER_WINS or yf_value in (None, "")


def _merge(
    yf_data: SnapshotFields, screener_data: SnapshotFields
) -> SnapshotFields:
    """Combine yfinance + screener per `_SCREENER_WINS` precedence."""
    merged: SnapshotFields = dict(yf_data)  # type: ignore[assignment]
    for key, value in screener_data.items():
        if _screener_preferred(key, value, merged.get(key)):
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
    """Attribute every populated field to the adapter whose value was kept."""
    out: dict[str, str] = {}
    for key in {**yf_data, **screener_data}:
        sc_value = screener_data.get(key)
        yf_value = yf_data.get(key)
        if _screener_preferred(key, sc_value, yf_value) and _present(sc_value):
            out[key] = "screener"
        elif _present(yf_value):
            out[key] = "yfinance"
    return out


def _present(value: object) -> bool:
    """Populated in a way worth attributing (not None / '' / empty container)."""
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def _detect_conflicts(
    yf_data: SnapshotFields, screener_data: SnapshotFields
) -> list[DataConflict]:
    """Scored fields where both adapters answered but disagree materially."""
    conflicts: list[DataConflict] = []
    for name in SCORED_FIELDS:
        yf_value = yf_data.get(name)
        sc_value = screener_data.get(name)
        if not isinstance(yf_value, (int, float)) or not isinstance(
            sc_value, (int, float)
        ):
            continue
        a, b = float(yf_value), float(sc_value)
        gap = abs(a - b)
        if gap < _CONFLICT_ABS_FLOOR.get(name, 0.0):
            continue
        scale = max(abs(a), abs(b))
        if scale == 0 or gap / scale < _CONFLICT_REL_THRESHOLD:
            continue
        if _screener_preferred(name, sc_value, yf_value):
            chosen = ("screener", b, "yfinance", a)
        else:
            chosen = ("yfinance", a, "screener", b)
        conflicts.append(
            DataConflict(
                field_name=name,
                chosen_source=chosen[0],
                chosen_value=chosen[1],
                other_source=chosen[2],
                other_value=chosen[3],
                rel_diff_pct=round(gap / scale * 100, 1),
            )
        )
    return conflicts
