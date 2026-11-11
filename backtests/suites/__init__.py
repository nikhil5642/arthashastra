"""One module per claim. Each defines `SUITE`; `ALL` lists them in report order.

Adding a backtest: write `suites/<name>.py` with an `evaluate(observations)`
and a `SUITE`, then list it below (a test fails if a module is left out).
"""

from __future__ import annotations

from backtests.suite import Suite
from backtests.suites import (
    bank_rubric,
    combined_score,
    dip_signals,
    early_concerns,
    early_strengths,
    fundamentals_score,
    growth_veto,
    peak_margin_guard,
    valuation_score,
    value_trap_guard,
    verdict_bands,
)

ALL: tuple[Suite, ...] = (
    fundamentals_score.SUITE,
    valuation_score.SUITE,
    combined_score.SUITE,
    verdict_bands.SUITE,
    growth_veto.SUITE,
    value_trap_guard.SUITE,
    peak_margin_guard.SUITE,
    early_concerns.SUITE,
    early_strengths.SUITE,
    dip_signals.SUITE,
    bank_rubric.SUITE,
)
