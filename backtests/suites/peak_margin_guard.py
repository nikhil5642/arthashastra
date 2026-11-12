"""Peak-margin guard — is a stock that is cheap on peak margins a trap?

The valuation pillar counts earnings only up to 1.5× the company's own
median net margin of the last fiscal years: a low P/E on peak-cycle
profit is not cheap. On each date "cheap" means the top third by reported
earnings yield among companies with positive earnings; within that, the
ones earning above their own cycle should return less than the rest.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats
from backtests.suites.value_trap_guard import cheapest_third


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    cheap = cheapest_third(observations)
    return beats(
        [o for o in cheap if o.peak_margin],
        [o for o in cheap if not o.peak_margin],
        claimed=Direction.NEGATIVE,
        labels=("Cheap on peak margins", "Cheap on normal margins"),
    )


SUITE = Suite(
    name="peak_margin_guard",
    claim="Among the cheapest third by earnings yield, companies earning above 1.5× their own median margin return less",
    evaluate=evaluate,
)
