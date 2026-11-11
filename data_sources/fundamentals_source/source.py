"""`FundamentalsSource` — the async facade over yfinance, screener.in and SEC EDGAR.

This is the only public entry point callers should use to build a
`BalanceSheetSnapshot`. It fans out to the adapters in parallel, merges
their outputs, and constructs the final snapshot.

Who supplies what:
  - yfinance      every listing: statements, quarters, prices, metadata.
  - screener.in   Indian listings: deeper tables, and it wins on the ratios
                  it publishes (`_SCREENER_WINS`).
  - SEC EDGAR     US filers: up to ten fiscal years of sales, profit and
                  margins, where Yahoo has four. History only — it never
                  overrides a current ratio.

Merge rules:
  - Non-finite floats (NaN / inf) are dropped from every input before
    anything else looks at them — a NaN that reaches a scorer slips past
    every `<` / `>=` comparison.
  - For any field in `_SCREENER_WINS`, screener wins if it emitted a
    value (even over a populated yfinance value). This is because
    screener's definitions are the canonical ones for Indian equities:
    "Borrowings" (not lease-inflated "Total Debt"), etc.
  - For everything else, yfinance fills gaps: any field screener didn't
    provide falls back to yfinance's value.
  - Every trailing-twelve-month figure describes ONE window: when screener
    sets it, yfinance's sums over a different window are dropped.
  - Every amount describes ONE currency: screener's statements are in
    rupees, so when Yahoo reports the same company in another currency
    (Infosys files in dollars) yfinance's absolute amounts are dropped.
    Ratios are currency-free and stay.

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
from data_sources.fundamentals_source import screener_adapter, sec_adapter, yfinance_adapter
from data_sources.fundamentals_source.snapshot import (
    BalanceSheetSnapshot,
    DataConflict,
    SCORED_FIELDS,
    SnapshotFields,
)
from data_sources.fundamentals_source.valuation import apply_valuation
from data_sources.fx_source import conversion_rate, major_unit
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
    "quarterly_operating_margin",
    "latest_yoy_revenue_growth",
    "latest_yoy_pat_growth",
    # Twelve months from screener's quarters, and the fiscal-year amounts
    # behind its cash-flow ratios.
    "ttm_period_end",
    "ttm_revenue",
    "ttm_net_income",
    "revenue",
    "operating_cash_flow",
    "capex",
    # Bank-specific metrics — always from screener's P&L / BS structure.
    "nim",
    "net_interest_income",
    "non_interest_income",
    "non_interest_income_ratio",
    "deposits",
    "borrowings",
    "credit_deposit_ratio",
    "is_bank",
    # Screener's statements are in rupees: its currencies describe the amounts kept.
    "currency",
    "financial_currency",
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
    "net_margin_annual_trend",
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
    """Async facade over live yfinance + screener.in (+ SEC EDGAR for US filers).

    Usage::

        source = FundamentalsSource(sec_user_agent=cfg.sec_user_agent)
        snapshot = await source.fetch("MSFT")                   # snapshot only
        result = await source.fetch_full("RELIANCE.NS")         # + price history

    `sec_user_agent` identifies the caller to SEC EDGAR, which asks every
    automated client to name itself; without one the SEC history is skipped.
    """

    def __init__(self, *, sec_user_agent: str = "") -> None:
        self._sec_user_agent = sec_user_agent

    async def fetch(self, ticker: str) -> BalanceSheetSnapshot:
        result = await self.fetch_full(ticker)
        return result.snapshot

    async def fetch_full(self, ticker: str) -> FetchResult:
        bare = ticker.strip().upper()
        log.info("fundamentals: live fetch for %s", bare)

        yf_result, screener_result, sec_result = await asyncio.gather(
            yfinance_adapter._fetch_yfinance(bare),
            screener_adapter._fetch_screener_in(bare),
            self._fetch_deep_history(bare),
            return_exceptions=True,
        )

        yf_data: SnapshotFields
        price_history: list[PriceRow]
        if isinstance(yf_result, BaseException):
            log.warning("yfinance failed for %s: %s", bare, yf_result)
            yf_data, price_history = SnapshotFields(), []
        else:
            yf_data, price_history = yf_result

        screener_data: SnapshotFields
        if isinstance(screener_result, BaseException):
            log.warning("screener.in failed for %s: %s", bare, screener_result)
            screener_data = SnapshotFields()
        else:
            screener_data = screener_result

        sec_data: SnapshotFields
        if isinstance(sec_result, BaseException):
            log.warning("SEC history failed for %s: %s", bare, sec_result)
            sec_data = SnapshotFields()
        else:
            sec_data = sec_result

        yf_data = _drop_non_finite(yf_data, label="yfinance")
        screener_data = _drop_non_finite(screener_data, label="screener")
        yf_data = _in_screeners_currency(yf_data, screener_data)
        deeper = _deeper_history(yf_data, sec_data)

        merged = _merge({**yf_data, **deeper}, screener_data)  # type: ignore[arg-type]
        _keep_one_trailing_window(merged, yf_data)
        merged["ticker"] = bare
        merged["as_of_date"] = date.today().isoformat()
        merged["source"] = _source_tag(yf_data, screener_data, deeper)

        allowed = set(BalanceSheetSnapshot.__dataclass_fields__)
        kwargs = {k: v for k, v in merged.items() if k in allowed}
        snap = BalanceSheetSnapshot(**kwargs)
        snap.field_sources = _field_sources(yf_data, screener_data) | {
            key: "sec" for key in deeper if key not in screener_data
        }
        snap.data_conflicts = _detect_conflicts(yf_data, screener_data)
        apply_valuation(
            snap,
            PriceSeries.from_rows(price_history),
            fx_to_reporting=await _fx_to_reporting(snap),
        )
        return FetchResult(snapshot=snap, price_history=price_history)

    async def _fetch_deep_history(self, symbol: str) -> SnapshotFields:
        """Up to ten fiscal years of sales, profit and margins for a US filer."""
        if not self._sec_user_agent or not sec_adapter.covers(symbol):
            return SnapshotFields()
        frames = await sec_adapter.fetch_annual_frames(symbol, user_agent=self._sec_user_agent)
        history = SnapshotFields()
        if frames is not None:
            yfinance_adapter._fill_annual_trends(history, frames.income, frames.balance)
        return history


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


# Absolute amounts (and per-share amounts) yfinance reports in the company's
# reporting currency. Everything else it supplies is a ratio, a percentage,
# a date, or a trading-currency price.
_YFINANCE_REPORTING_AMOUNTS: tuple[str, ...] = (
    "net_income",
    "free_cash_flow",
    "shareholders_equity",
    "revenue",
    "operating_cash_flow",
    "capex",
    "stock_based_compensation",
    "eps_history",
    "book_value_per_share",
    "quarterly_revenue",
    "quarterly_pat",
    "sales_annual_trend",
    "net_profit_annual_trend",
    "ttm_period_end",
    "ttm_revenue",
    "ttm_net_income",
    "ttm_operating_cash_flow",
    "ttm_capex",
    "ttm_free_cash_flow",
    "ttm_stock_based_compensation",
)


def _in_screeners_currency(
    yf_data: SnapshotFields, screener_data: SnapshotFields
) -> SnapshotFields:
    """yfinance's fields, minus its amounts when they are in another currency than screener's.

    Mixing them would put dollars into rupee statements: a free cash flow
    in USD over a market cap in INR is off by the exchange rate.
    """
    screener_currency = screener_data.get("financial_currency")
    yf_currency = yf_data.get("financial_currency")
    if not screener_currency or not yf_currency or screener_currency == yf_currency:
        return yf_data
    log.info(
        "yfinance reports in %s, screener in %s — keeping yfinance's ratios only",
        yf_currency,
        screener_currency,
    )
    return {k: v for k, v in yf_data.items() if k not in _YFINANCE_REPORTING_AMOUNTS}  # type: ignore[return-value]


def _deeper_history(yf_data: SnapshotFields, sec_data: SnapshotFields) -> SnapshotFields:
    """SEC's fiscal-year series, where they reach further back than Yahoo's."""
    deeper: dict[str, object] = {}
    for key, series in sec_data.items():
        current = yf_data.get(key)
        if isinstance(series, list) and len(series) > (len(current) if isinstance(current, list) else 0):
            deeper[key] = series
    return deeper  # type: ignore[return-value]


# Trailing figures only yfinance supplies. They are sums over ITS four
# newest quarters, so they are dropped whenever the merged window is not
# that same one (screener is often a quarter ahead right after results).
_YFINANCE_TRAILING_FIELDS: tuple[str, ...] = (
    "ttm_operating_cash_flow",
    "ttm_capex",
    "ttm_free_cash_flow",
    "ttm_stock_based_compensation",
    "core_earnings_ratio_ttm",
)


def _keep_one_trailing_window(merged: SnapshotFields, yf_data: SnapshotFields) -> None:
    """Drop yfinance's twelve-month sums if the merged window is a different one."""
    if merged.get("ttm_period_end") == yf_data.get("ttm_period_end"):
        return
    for key in _YFINANCE_TRAILING_FIELDS:
        merged.pop(key, None)  # type: ignore[misc]


async def _fx_to_reporting(snap: BalanceSheetSnapshot) -> float | None:
    """Reporting-currency units per unit of the trading currency's major unit.

    Only fetched when the two currencies differ; None when either is
    unknown or Yahoo quotes no rate.
    """
    if not snap.currency or not snap.financial_currency:
        return None
    trading = major_unit(snap.currency)[0]
    reporting = snap.financial_currency.upper()
    if trading == reporting:
        return None
    return await conversion_rate(trading, reporting)


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
    yf_data: SnapshotFields, screener_data: SnapshotFields, sec_history: SnapshotFields
) -> str:
    """Annotate `snap.source` so the reader can see which adapters fired."""
    fired = [
        name
        for name, data in (("yfinance", yf_data), ("screener", screener_data), ("sec", sec_history))
        if data
    ]
    return "+".join(fired) or "empty"


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
