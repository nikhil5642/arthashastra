"""Early strengths — does a flagged strength come before a higher return?

The mirror of `early_concerns`: companies showing a material strength
(ALERT or URGENT) should return more than companies showing none. On
fiscal-year history the strengths that can fire are debt falling sharply
and the share count shrinking; the quarterly and shareholding strengths
are replayed only for Indian listings over screener's last three years.
The group is small, so read its size before its t-stat.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    return beats(
        [o for o in observations if o.material_strengths > 0],
        [o for o in observations if o.material_strengths == 0],
        claimed=Direction.POSITIVE,
        labels=("Showing a material strength", "No material strength"),
    )


SUITE = Suite(
    name="early_strengths",
    claim="A company showing a material early strength returns more than one showing none",
    evaluate=evaluate,
)
