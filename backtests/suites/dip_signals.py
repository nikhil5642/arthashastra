"""Dip signals — does buying a quality company's dip pay?

`pick` buys companies scoring 75+ that have fallen 20%+ from their
52-week high, with two technical confirmations. Replayed with the
deterministic composite as the quality bar, a dip signal should be
followed by a higher return than everything else on the same dates.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    return beats(
        [o for o in observations if o.dip_signal],
        [o for o in observations if not o.dip_signal],
        claimed=Direction.POSITIVE,
        labels=("Dip signal fired", "Everything else"),
    )


SUITE = Suite(
    name="dip_signals",
    claim="A quality-dip signal is followed by a higher return than everything else",
    evaluate=evaluate,
)
