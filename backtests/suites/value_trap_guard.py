"""Value-trap guard — is a cheap shrinking stock worse than a cheap growing one?

The valuation pillar caps a slow grower's valuation score at 70: a stock
that is cheap because the business is shrinking is not a bargain. On each
date "cheap" means the top third by earnings yield among companies with
positive earnings; within that, slow growers should return less than
growers.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    cheap = cheapest_third(observations)
    return beats(
        [o for o in cheap if o.slow_growth],
        [o for o in cheap if not o.slow_growth],
        claimed=Direction.NEGATIVE,
        labels=("Cheap slow growers", "Cheap growers"),
    )


def cheapest_third(observations: Sequence[Observation]) -> list[Observation]:
    """Per date, the third of profitable companies with the highest earnings yield."""
    by_date: dict[date, list[Observation]] = defaultdict(list)
    for obs in observations:
        if obs.earnings_yield is not None and obs.earnings_yield > 0:
            by_date[obs.as_of].append(obs)
    cheap: list[Observation] = []
    for group in by_date.values():
        ranked = sorted(group, key=lambda o: o.earnings_yield or 0.0, reverse=True)
        cheap.extend(ranked[: len(ranked) // 3])
    return cheap


SUITE = Suite(
    name="value_trap_guard",
    claim="Among the cheapest third by earnings yield, slow growers return less than growers",
    evaluate=evaluate,
)
