"""Point-in-time snapshots — what the fundamentals looked like on a past date.

A backtest may only use data that was public on its as-of date. A fiscal
year's annual numbers count as public `reporting_lag_days` after the
year ends (default 90: listed companies in India and the US publish
audited annual results within ~60–90 days). Each snapshot is rebuilt by
running the SAME formulas the live pipeline uses — screener's appliers,
or the yfinance fillers — on tables truncated to the visible columns, so
live scoring and backtest scoring can't drift apart.

Depth is whatever the sources publish:

  - SEC EDGAR (US filers):     ~18 fiscal years, first-filed values
  - screener.in (India):       ~12 fiscal years
  - yfinance (everything else): 4 fiscal years

Prices go back much further (Microsoft to 1986), so returns are never the
binding constraint. Values that only exist as a current number (the 52-week
range, a published P/E, analyst targets) are never back-filled.

Quarters: SEC and Yahoo frames are fiscal years only, so for most listings
a past snapshot carries no quarterly figure — no trailing twelve months, no
quarter-on-quarter trend. Screener's quarterly and shareholding tables are
replayed for Indian listings, column by column under the same reporting
lag, as far back as screener publishes them (about three years).

Sector / industry are taken from today: they only route the rubric and
rarely change.

Every per-share number is put on today's share basis, the basis of Yahoo's
split-adjusted prices. screener and Yahoo already restate past EPS for
later splits and bonus issues; SEC's first-filed EPS is rebased here.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

from data_sources.fundamentals_source import screener_adapter, sec_adapter, yfinance_adapter
from data_sources.fundamentals_source.primitives import (
    _latest_with_label,
    _screener_month_to_iso,
    _value_at_label,
)
from data_sources.fundamentals_source.screener_adapter import Table
from data_sources.fundamentals_source.snapshot import BalanceSheetSnapshot, SnapshotFields
from data_sources.fundamentals_source.source import _drop_non_finite, _merge
from data_sources.fundamentals_source.valuation import apply_valuation
from data_sources.fundamentals_source.yfinance_adapter import SplitRow
from data_sources.price_series import PriceSeries

if TYPE_CHECKING:
    from pandas import DataFrame

log = logging.getLogger(__name__)

DEFAULT_REPORTING_LAG_DAYS = 90

PriceRow = tuple[str, float]

# Fields copied from today's metadata into every historical snapshot.
_IDENTITY_FIELDS: tuple[str, ...] = (
    "company_name",
    "sector",
    "industry",
    "country",
    "exchange",
    "currency",
    "financial_currency",
)


@dataclass(slots=True)
class AnnualFrames:
    """yfinance-shaped annual statements (rows × fiscal-year columns, newest first)."""

    income: "DataFrame | None"
    balance: "DataFrame | None"
    cashflow: "DataFrame | None"
    source: str  # "sec" or "yfinance"


@dataclass(slots=True)
class StatementHistory:
    """Everything needed to rebuild one listing's snapshot on any past date."""

    symbol: str
    prices: list[PriceRow] = field(default_factory=list)  # split-adjusted closes, oldest first
    total_return: list[PriceRow] = field(default_factory=list)  # dividends folded in too
    frames: AnnualFrames | None = None
    screener: dict[str, Table] = field(default_factory=dict)
    metadata: SnapshotFields = field(default_factory=SnapshotFields)
    splits: list[SplitRow] = field(default_factory=list)  # every stock split, oldest first

    @property
    def fundamentals_source(self) -> str:
        parts = [self.frames.source] if self.frames is not None else []
        if self.screener:
            parts.append("screener")
        return "+".join(parts) or "none"


async def fetch_statement_history(symbol: str, *, sec_user_agent: str) -> StatementHistory:
    """Fetch prices, statements, and metadata for one listing, all sources at once."""
    bare = symbol.strip().upper()
    yahoo, tables, sec = await asyncio.gather(
        yfinance_adapter.fetch_history_bundle(bare),
        screener_adapter.fetch_tables(bare),
        sec_adapter.fetch_annual_frames(bare, user_agent=sec_user_agent),
        return_exceptions=True,
    )
    history = StatementHistory(symbol=bare)
    splits: list[SplitRow] = []
    if isinstance(yahoo, yfinance_adapter.YahooHistory):
        history.prices = yahoo.prices
        history.total_return = yahoo.total_return
        history.metadata = yahoo.metadata
        history.frames = AnnualFrames(yahoo.income, yahoo.balance, yahoo.cashflow, "yfinance")
        history.splits = splits = yahoo.splits
    else:
        log.warning("yfinance history failed for %s: %s", bare, yahoo)
    if isinstance(sec, sec_adapter.SecAnnualFrames):
        # SEC's as-filed history is deeper than Yahoo's 4 years: it wins.
        rebase_sec_eps(sec, splits)
        history.frames = AnnualFrames(sec.income, sec.balance, sec.cashflow, "sec")
    elif isinstance(sec, Exception):
        log.warning("SEC history failed for %s: %s", bare, sec)
    if isinstance(tables, dict):
        history.screener = tables
    else:
        log.warning("screener history failed for %s: %s", bare, tables)
    return history


def rebase_sec_eps(frames: sec_adapter.SecAnnualFrames, splits: Sequence[SplitRow]) -> None:
    """Put SEC's first-filed EPS on today's share basis, in place.

    Each split after an EPS was filed divides it by the split's ratio:
    Apple filed FY2013 EPS of $39.75; after its 7:1 (2014) and 4:1 (2020)
    splits that is $1.42 a share today. Splits before the filing date are
    already in the reported number.
    """
    if "Diluted EPS" not in frames.income.index:
        return
    events = [(date.fromisoformat(day), ratio) for day, ratio in splits]
    for column in frames.income.columns:
        filed = frames.eps_filed.get(column.date())
        eps = frames.income.at["Diluted EPS", column]
        if filed is None or math.isnan(eps):
            continue
        factor = math.prod(ratio for day, ratio in events if day > filed)
        frames.income.at["Diluted EPS", column] = eps / factor


def snapshot_as_of(
    history: StatementHistory,
    as_of: date,
    *,
    reporting_lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
    prices: PriceSeries | None = None,
) -> BalanceSheetSnapshot | None:
    """The snapshot as it could have been built on `as_of`; None if nothing was public.

    Valuation is priced at the close on `as_of`. Pass `prices` (built once
    from `history.prices`) when rebuilding many dates for one listing.
    """
    cutoff = as_of - timedelta(days=reporting_lag_days)
    frame_fields = _drop_non_finite(
        _frames_as_of(history.frames, cutoff, history.splits), label="frames"
    )
    screener_fields = _drop_non_finite(_screener_as_of(history.screener, cutoff), label="screener")
    merged = _merge(frame_fields, screener_fields)
    for key in _IDENTITY_FIELDS:
        value = history.metadata.get(key)
        if value:
            merged.setdefault(key, value)  # type: ignore[misc]

    allowed = set(BalanceSheetSnapshot.__dataclass_fields__)
    snap = BalanceSheetSnapshot(
        ticker=history.symbol,
        as_of_date=as_of.isoformat(),
        **{k: v for k, v in merged.items() if k in allowed and k not in ("ticker", "as_of_date")},
    )
    if snap.data_completeness_pct == 0:
        return None
    apply_valuation(
        snap, prices if prices is not None else PriceSeries.from_rows(history.prices), as_of=as_of
    )
    return snap


# ── Statement frames (SEC or yfinance) ───────────────────────────────────────


def _frames_as_of(
    frames: AnnualFrames | None, cutoff: date, splits: list[SplitRow]
) -> SnapshotFields:
    out = SnapshotFields()
    if frames is None:
        return out
    income = _visible_frame(frames.income, cutoff)
    balance = _visible_frame(frames.balance, cutoff)
    cashflow = _visible_frame(frames.cashflow, cutoff)
    if income is None and balance is None:
        return out
    yfinance_adapter._fill_annual_period(out, income)
    yfinance_adapter._fill_eps_history(out, income)
    yfinance_adapter._fill_balance_sheet_derived(out, balance)
    yfinance_adapter._fill_income_statement_derived(out, income, balance)
    yfinance_adapter._fill_cash_flow_derived(out, cashflow, income)
    yfinance_adapter._fill_working_capital_days(out, income, balance)
    yfinance_adapter._fill_asset_composition(out, balance)
    yfinance_adapter._fill_annual_trends(out, income, balance)
    # No quarterly history exists point-in-time: fiscal year-end against fiscal year-end.
    yfinance_adapter._fill_balance_sheet_changes(out, None, balance, splits)
    return out


def _visible_frame(df: "DataFrame | None", cutoff: date) -> "DataFrame | None":
    """Only the fiscal-year columns that ended on or before `cutoff`."""
    if df is None or df.empty:
        return None
    visible = [c for c in df.columns if hasattr(c, "date") and c.date() <= cutoff]
    return df[visible] if visible else None


# ── Screener tables (Indian listings) ────────────────────────────────────────


def _screener_as_of(tables: dict[str, Table], cutoff: date) -> SnapshotFields:
    out = SnapshotFields()
    if not tables:
        return out
    pl_headers, pl_rows = _visible_table(tables.get("profit-loss"), cutoff)
    if not pl_headers:
        return out
    bs_headers, bs_rows = _visible_table(tables.get("balance-sheet"), cutoff)
    cf_headers, cf_rows = _visible_table(tables.get("cash-flow"), cutoff)
    _, ratio_rows = _visible_table(tables.get("ratios"), cutoff)
    q_headers, q_rows = _visible_table(tables.get("quarters"), cutoff)
    sh_headers, sh_rows = _visible_table(tables.get("shareholding"), cutoff)

    screener_adapter._apply_balance_sheet(out, bs_headers, bs_rows)
    screener_adapter._apply_profit_loss(out, pl_headers, pl_rows)
    screener_adapter._apply_cash_flow(out, cf_headers, cf_rows, pl_headers, pl_rows)
    screener_adapter._apply_ratios(out, ratio_rows)
    screener_adapter._apply_quarters(out, q_headers, q_rows)
    screener_adapter._apply_asset_quality(out, q_rows)
    screener_adapter._apply_derived(out, bs_headers, bs_rows, pl_headers, pl_rows)
    screener_adapter._apply_bank_metrics(out, bs_headers, bs_rows, pl_headers, pl_rows)
    screener_adapter._apply_multi_year_trends(
        out, bs_headers, bs_rows, pl_headers, pl_rows, ratio_rows
    )
    screener_adapter._apply_shareholding_trend(out, sh_headers, sh_rows)
    _apply_historical_top_ratios(out, bs_headers, bs_rows, pl_headers, pl_rows, ratio_rows)
    return out


def _apply_historical_top_ratios(
    out: SnapshotFields,
    bs_headers: list[str],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
    ratio_rows: dict[str, list[float | None]],
) -> None:
    """ROE, ROCE, and insider holding as of the cutoff.

    Live, these come from screener's top-of-page ratios, which only show
    today's values; historically they are rebuilt from the tables.
    """
    latest = _latest_with_label(pl_headers, pl_rows.get("net profit"))
    if latest is not None:
        label, net_profit = latest
        capital = _value_at_label(bs_headers, bs_rows.get("equity capital"), label)
        reserves = _value_at_label(bs_headers, bs_rows.get("reserves"), label)
        if capital is not None and reserves is not None and capital + reserves > 0:
            out["roe"] = round(net_profit / (capital + reserves) * 100, 2)
    roce_trend = out.get("roce_annual_trend") or []
    if roce_trend:
        out["roce"] = roce_trend[-1]
    insider_trend = out.get("insider_holding_trend") or []
    if insider_trend:
        out["insider_holding"] = insider_trend[0]


def _visible_table(table: Table | None, cutoff: date) -> Table:
    """Only the columns whose period ended on or before `cutoff` (TTM never)."""
    if not table:
        return [], {}
    headers, rows = table
    keep = [i for i, header in enumerate(headers) if _period_end_on_or_before(header, cutoff)]
    return (
        [headers[i] for i in keep],
        {label: [values[i] if i < len(values) else None for i in keep] for label, values in rows.items()},
    )


def _period_end_on_or_before(header: str, cutoff: date) -> bool:
    iso = _screener_month_to_iso(header)
    return iso is not None and date.fromisoformat(iso) <= cutoff
