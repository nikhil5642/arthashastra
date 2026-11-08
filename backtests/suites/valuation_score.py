"""Valuation score — does a cheaper price go on to return more?

The valuation pillar scores the price against what the business earns
(earnings yield, FCF yield or P/B vs ROE, P/E vs its own history). It was
added because the first 30-year backtest showed the fundamentals pillar
alone ignored price. Only observations where valuation could be scored
(price and statements in one currency) take part.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation, ScoreKind
from backtests.suite import Suite, SuiteResult, ranks_returns


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    return ranks_returns(observations, ScoreKind.VALUATION)


SUITE = Suite(
    name="valuation_score",
    claim="A higher valuation score (a cheaper price) is followed by a higher return",
    evaluate=evaluate,
)
