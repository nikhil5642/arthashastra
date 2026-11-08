"""Combined score — does adding valuation improve the ranking?

The composite blends fundamentals (35%) and valuation (15%). That only
earns its place if the blend ranks next-period returns better than
fundamentals alone. Tested on the same dates, pairing each date's two
rank ICs, so a good or bad year counts for both sides equally.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import (
    Observation,
    ScoreKind,
    cross_sectional_ics,
    ic_difference,
    mean_test,
)
from backtests.suite import (
    MIN_DATES,
    Direction,
    ResultTable,
    Suite,
    SuiteResult,
    judge,
    mean_test_finding,
    percent,
    signed,
)


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    improvement = ic_difference(observations, ScoreKind.COMBINED, ScoreKind.FUNDAMENTALS)
    rows: list[tuple[str, ...]] = []
    for kind in (ScoreKind.FUNDAMENTALS, ScoreKind.VALUATION, ScoreKind.COMBINED):
        ic = mean_test([d.ic for d in cross_sectional_ics(observations, kind)])
        rows.append(
            (kind.value, str(ic.n), signed(ic.mean, 3), signed(ic.t_stat, 2), percent(ic.positive_share))
        )
    return SuiteResult(
        outcome=judge(
            improvement.mean,
            improvement.t_stat,
            claimed=Direction.POSITIVE,
            enough_data=improvement.n >= MIN_DATES,
        ),
        finding=mean_test_finding(improvement, "rank IC gained by adding valuation"),
        table=ResultTable(
            columns=("Score", "Dates", "Mean rank IC", "t-stat", "Positive dates"),
            rows=rows,
        ),
    )


SUITE = Suite(
    name="combined_score",
    claim="Fundamentals + valuation ranks returns better than fundamentals alone",
    evaluate=evaluate,
)
