"""Verdict bands — does a BUY actually beat an AVOID?

Verdicts are what a reader acts on, so the bands must order returns, not
just the underlying score. Tested on the deterministic composite
(fundamentals + valuation at their live relative weights): BUY or better
against AVOID or worse, on excess returns.
"""

from __future__ import annotations

from collections.abc import Sequence

from backtests.engine.metrics import Observation, ScoreKind, band_returns, compare_groups
from backtests.suite import (
    RETURN_COLUMNS,
    Direction,
    ResultTable,
    Suite,
    SuiteResult,
    comparison_finding,
    judge_comparison,
    return_cells,
)
from scorers.base import Verdict, verdict_for

_BUY_OR_BETTER = frozenset({Verdict.STRONG_BUY, Verdict.BUY})
_AVOID_OR_WORSE = frozenset({Verdict.AVOID, Verdict.RED_FLAG})


def evaluate(observations: Sequence[Observation]) -> SuiteResult:
    comparison = compare_groups(
        [o for o in observations if verdict_for(o.combined) in _BUY_OR_BETTER],
        [o for o in observations if verdict_for(o.combined) in _AVOID_OR_WORSE],
    )
    return SuiteResult(
        outcome=judge_comparison(comparison, claimed=Direction.POSITIVE),
        finding=comparison_finding(comparison, ("BUY or better", "AVOID or worse")),
        table=ResultTable(
            columns=("Verdict", *RETURN_COLUMNS),
            rows=[
                (row.verdict.value, *return_cells(row.stats))
                for row in band_returns(observations, ScoreKind.COMBINED)
            ],
        ),
    )


SUITE = Suite(
    name="verdict_bands",
    claim="A BUY-or-better deterministic composite beats an AVOID-or-worse one",
    evaluate=evaluate,
)
