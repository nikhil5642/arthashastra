"""Growth veto — do slow growers really underperform?

The industrial rubric caps the fundamentals score of a company whose
3-year revenue and profit growth are both below 7% (70, tightening to 60
and 50 below 5% and 3%). That anti-optimism anchor is only justified if
those companies go on to return less than other industrials. Banks are
left out: their rubric has no growth veto.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    industrial = [o for o in observations if not o.is_bank]
    return beats(
        [o for o in industrial if o.slow_growth],
        [o for o in industrial if not o.slow_growth],
        claimed=Direction.NEGATIVE,
        labels=("Slow growers (vetoed)", "Other industrials"),
    )


SUITE = Suite(
    name="growth_veto",
    claim="Industrials in growth-veto territory (both 3y CAGRs < 7%) return less than the rest",
    evaluate=evaluate,
)
