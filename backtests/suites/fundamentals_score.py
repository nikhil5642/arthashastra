"""Fundamentals score — do better businesses go on to return more?

The fundamentals pillar scores business quality from the statements
alone and is the largest weight in the composite (35%). If it carries
information, ranking companies by it on any date should line up with
their returns over the next period.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation, ScoreKind
from backtests.suite import Suite, SuiteResult, ranks_returns


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    return ranks_returns(observations, ScoreKind.FUNDAMENTALS)


SUITE = Suite(
    name="fundamentals_score",
    claim="A higher fundamentals score is followed by a higher return (rank IC > 0)",
    evaluate=evaluate,
)
