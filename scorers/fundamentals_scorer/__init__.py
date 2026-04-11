"""Deterministic fundamentals scorer — dispatches to industrial or bank rubric.

The public API is a single function::

    score_fundamentals(snap: BalanceSheetSnapshot) -> ScorerResult

It takes a sparse `BalanceSheetSnapshot`; missing components are dropped
and remaining weights rescaled, so a sparse snapshot still produces a
meaningful score (flagged via `ScorerResult.data_completeness_pct`).

Dispatch is based on `is_bank_snapshot(snap)` — the snapshot carries an
`is_bank` flag set by the screener parser when the P&L has the bank row
shape, plus a fallback keyword match on sector / industry strings for
yfinance-only fetches. See:
  - `industrial.py` for the industrial rubric (most equities)
  - `bank.py`        for the Bank / NBFC / Financial Services rubric
"""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot, is_bank_snapshot
from scorers.base import ScorerResult
from scorers.fundamentals_scorer import bank, industrial


def score_fundamentals(snap: BalanceSheetSnapshot) -> ScorerResult:
    """Route to the appropriate rubric based on the snapshot's sector."""
    if is_bank_snapshot(snap):
        return bank.score(snap)
    return industrial.score(snap)


__all__ = ["score_fundamentals"]
