"""Bank rubric — does it rank banks and lenders?

Banks are scored on their own rubric (ROA / NIM curves, asset quality,
PAT growth, franchise), because industrial ratios mean nothing for a
lender. That rubric is only useful if, among banks alone, a higher score
comes before a higher return.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation, ScoreKind
from backtests.suite import Suite, SuiteResult, ranks_returns


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    return ranks_returns([o for o in observations if o.is_bank], ScoreKind.FUNDAMENTALS)


SUITE = Suite(
    name="bank_rubric",
    claim="Among banks and lenders, a higher bank-rubric score is followed by a higher return",
    evaluate=evaluate,
)
