"""Early concerns — does a flagged company go on to return less?

The early-signal layer names concerns the scored ratios don't show yet.
That is only worth reading if companies carrying a material concern
(ALERT or URGENT) return less than companies carrying none.

Only the concerns the history can replay are tested: the ones built on
fiscal-year statements (a margin above its own median, debt or goodwill
jumping year on year, negative equity, dilution) and, for Indian listings
over screener's last three years, the quarterly and shareholding ones.
News, price-drawdown and trailing-cash-flow concerns have no point-in-time
history, so this suite says nothing about them.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    return beats(
        [o for o in observations if o.material_concerns > 0],
        [o for o in observations if o.material_concerns == 0],
        claimed=Direction.NEGATIVE,
        labels=("Carrying a material concern", "No material concern"),
    )


SUITE = Suite(
    name="early_concerns",
    claim="A company carrying a material early concern returns less than one carrying none",
    evaluate=evaluate,
)
