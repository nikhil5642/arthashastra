"""Priced-for-doubt cap — is a profit at under 5× earnings a bargain or a question?

The valuation pillar holds the score at 70 when the price is under
`DOUBT_PE` (5) times the earnings it counts (one-offs out, cyclical peaks
capped): a market that pays that little for a real profit usually doubts
the profit, the accounts, or that the money can reach shareholders. The
cap is justified if those companies return no more than the next-cheapest
band, 5–10× repeatable earnings; if they return clearly more, the cap is
throwing away a real signal.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation
from backtests.suite import Direction, Suite, SuiteResult, beats
from scorers.valuation_scorer import DOUBT_PE

COMPARISON_PE = 10.0  # the honest-cheap band the doubted group is set against


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    cheap = [o for o in observations if o.repeatable_pe is not None and o.repeatable_pe < COMPARISON_PE]
    return beats(
        [o for o in cheap if (o.repeatable_pe or 0.0) < DOUBT_PE],
        [o for o in cheap if (o.repeatable_pe or 0.0) >= DOUBT_PE],
        claimed=Direction.NEGATIVE,
        labels=(f"Under {DOUBT_PE:g}× repeatable earnings", f"{DOUBT_PE:g}–{COMPARISON_PE:g}×"),
    )


SUITE = Suite(
    name="priced_for_doubt",
    claim=(
        f"Profitable companies under {DOUBT_PE:g}× repeatable earnings return no more than "
        f"those at {DOUBT_PE:g}–{COMPARISON_PE:g}×"
    ),
    evaluate=evaluate,
)
