"""Fundamentals data source — live yfinance + screener.in.

This package is split along the axes you'd expect:

  primitives.py        — scalar/series parsing helpers (pure functions)
  snapshot.py          — `BalanceSheetSnapshot` dataclass, `SCORED_FIELDS`,
                         `is_bank_snapshot` sector check
  yfinance_adapter.py  — yfinance fetcher (fills ratios screener lacks)
  screener_adapter.py  — screener.in fetcher + section appliers (authoritative)
  source.py            — `FundamentalsSource` async facade + merge logic

Callers import from `data_sources.fundamentals_source` (this package) and
get the public API below. Nothing outside this directory should reach
into the submodules, except tests that monkeypatch adapter functions at
their definition site (`yfinance_adapter._fetch_yfinance_sync`,
`screener_adapter._fetch_screener_in`).
"""

from data_sources.fundamentals_source.snapshot import (
    BalanceSheetSnapshot,
    SCORED_FIELDS,
    is_bank_snapshot,
)
from data_sources.fundamentals_source.source import (
    FetchResult,
    FundamentalsSource,
    PriceRow,
    _field_sources,
    _merge,
    _source_tag,
    _SCREENER_WINS,
)

# Re-export parsing primitives so existing tests importing them from
# `data_sources.fundamentals_source` keep working without churn.
from data_sources.fundamentals_source.primitives import (
    HTTP_TIMEOUT_SECONDS,
    SCREENER_BASE_URL,
    SCREENER_USER_AGENT,
    _drop_ttm,
    _latest,
    _parse_float,
    _parse_pct,
    _screener_cagr,
    _screener_month_to_iso,
)

# Re-export the adapter appliers so existing tests can exercise them
# without reaching into the submodules.
from data_sources.fundamentals_source.screener_adapter import (
    _apply_balance_sheet,
    _apply_profit_loss,
    _fetch_screener_in,
    _parse_section,
)
from data_sources.fundamentals_source.yfinance_adapter import (
    _fetch_yfinance,
    _fetch_yfinance_sync,
)

__all__ = [
    # Public API
    "BalanceSheetSnapshot",
    "FetchResult",
    "FundamentalsSource",
    "PriceRow",
    "SCORED_FIELDS",
    "is_bank_snapshot",
    # Transport constants
    "HTTP_TIMEOUT_SECONDS",
    "SCREENER_BASE_URL",
    "SCREENER_USER_AGENT",
]
