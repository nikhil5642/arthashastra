"""Fundamentals data source — yfinance for every listing, screener.in for
Indian listings, SEC EDGAR for US backtest history.

This package is split along the axes you'd expect:

  primitives.py        — scalar/series parsing helpers (pure functions)
  snapshot.py          — `BalanceSheetSnapshot` dataclass, `SCORED_FIELDS`,
                         `is_bank_snapshot` rubric router
  yfinance_adapter.py  — yfinance fetcher (the global baseline)
  screener_adapter.py  — screener.in fetcher + section appliers (wins for .NS / .BO)
  sec_adapter.py       — SEC EDGAR companyfacts → as-filed annual frames
  valuation.py         — price-vs-statement ratios (P/E, FCF yield, P/B, P/E history),
                         spot FX for cross-currency listings, receipt detection
  history.py           — point-in-time snapshots for backtests
  source.py            — `FundamentalsSource` async facade + merge logic
                         (SEC adds up to ten fiscal years of history for US filers)

Callers import the public API below from this package. Tests that need a
private helper import it from the submodule that defines it, and
monkeypatch adapter functions at their definition site
(`yfinance_adapter._fetch_yfinance_sync`, `screener_adapter._fetch_screener_in`).
"""

from data_sources.fundamentals_source.snapshot import (
    BalanceSheetSnapshot,
    DataConflict,
    SCORED_FIELDS,
    StatementWindow,
    is_bank_snapshot,
    is_financial_label,
    is_insurer_label,
)
from data_sources.fundamentals_source.source import (
    FetchResult,
    FundamentalsSource,
    PriceRow,
)
from data_sources.fundamentals_source.primitives import (
    HTTP_TIMEOUT_SECONDS,
    PROFIT_WIPED_OUT_CAGR,
    SCREENER_BASE_URL,
    SCREENER_USER_AGENT,
    change_by_date,
    latest_yoy_growth,
    pct_change_by_date,
)

__all__ = [
    # Public API
    "BalanceSheetSnapshot",
    "DataConflict",
    "FetchResult",
    "FundamentalsSource",
    "PROFIT_WIPED_OUT_CAGR",
    "PriceRow",
    "SCORED_FIELDS",
    "StatementWindow",
    "change_by_date",
    "is_bank_snapshot",
    "is_financial_label",
    "is_insurer_label",
    "latest_yoy_growth",
    "pct_change_by_date",
    # Transport constants
    "HTTP_TIMEOUT_SECONDS",
    "SCREENER_BASE_URL",
    "SCREENER_USER_AGENT",
]
