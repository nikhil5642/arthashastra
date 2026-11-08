"""Suite framework tests — the judging rules and the two claim shapes."""

from __future__ import annotations

import math
from datetime import date

from backtests.engine.metrics import Observation, ScoreKind, attach_excess_returns, mean_test
from backtests.suite import (
    MIN_GROUP_SIZE,
    Direction,
    Outcome,
    beats,
    judge,
    mean_test_finding,
    ranks_returns,
)


def test_judge_rules():
    up, down = Direction.POSITIVE, Direction.NEGATIVE
    assert judge(0.05, 2.5, claimed=up, enough_data=True) is Outcome.PASS
    assert judge(0.05, 1.2, claimed=up, enough_data=True) is Outcome.WEAK_PASS
    assert judge(0.05, None, claimed=up, enough_data=True) is Outcome.WEAK_PASS
    assert judge(-0.01, -0.3, claimed=up, enough_data=True) is Outcome.WEAK_FAIL  # noise, not proof
    assert judge(0.0, None, claimed=up, enough_data=True) is Outcome.WEAK_FAIL
    assert judge(-0.08, -2.4, claimed=up, enough_data=True) is Outcome.FAIL
    assert judge(-4.0, -3.0, claimed=down, enough_data=True) is Outcome.PASS
    assert judge(4.0, 3.0, claimed=down, enough_data=True) is Outcome.FAIL
    assert judge(0.3, 9.0, claimed=up, enough_data=False) is Outcome.NO_DATA
    assert judge(None, None, claimed=up, enough_data=True) is Outcome.NO_DATA
    assert judge(0.2, math.inf, claimed=up, enough_data=True) is Outcome.PASS


def _ranked(direction: int, tickers: int = 12) -> list[Observation]:
    """Eight years × `tickers` names; returns rise (or fall) with the score, plus noise."""
    observations = [
        Observation(
            ticker=f"T{j}", as_of=date(2010 + i, 12, 31), is_bank=False, fundamentals=30.0 + 5 * j,
            completeness=100.0, valuation=None,
            forward_return_pct=direction * 3.0 * j + ((i * 7 + j * 3) % 5) - 2,
        )
        for i in range(8)
        for j in range(tickers)
    ]
    attach_excess_returns(observations)
    return observations


def test_ranks_returns_passes_fails_and_needs_dates():
    passed = ranks_returns(_ranked(+1), ScoreKind.FUNDAMENTALS)
    assert passed.outcome is Outcome.PASS
    assert "over 8 dates" in passed.finding and "top third minus bottom third: +" in passed.finding
    assert passed.table is not None and [row[0] for row in passed.table.rows] == [
        "Bottom third", "Middle third", "Top third",
    ]
    assert ranks_returns(_ranked(-1), ScoreKind.FUNDAMENTALS).outcome is Outcome.FAIL

    few_dates = [o for o in _ranked(+1) if o.as_of.year < 2013]
    assert ranks_returns(few_dates, ScoreKind.FUNDAMENTALS).outcome is Outcome.NO_DATA
    too_few_names = ranks_returns(_ranked(+1, tickers=8), ScoreKind.FUNDAMENTALS)
    assert too_few_names.outcome is Outcome.NO_DATA
    assert "needs 10+ scored tickers" in too_few_names.finding
    assert "the most on any date was 8" in too_few_names.finding


def test_beats_compares_excess_returns_and_needs_group_size():
    observations = _ranked(+1)
    top = [o for o in observations if o.fundamentals >= 60]
    rest = [o for o in observations if o.fundamentals < 60]
    result = beats(top, rest, claimed=Direction.POSITIVE, labels=("Top half", "Bottom half"))
    assert result.outcome is Outcome.PASS
    assert result.finding.startswith("Top half returned +")
    assert "48 vs 48 observations" in result.finding

    reversed_claim = beats(top, rest, claimed=Direction.NEGATIVE, labels=("Top half", "Bottom half"))
    assert reversed_claim.outcome is Outcome.FAIL

    small = beats(top[: MIN_GROUP_SIZE - 1], rest, claimed=Direction.POSITIVE, labels=("A", "B"))
    assert small.outcome is Outcome.NO_DATA
    empty = beats([], rest, claimed=Direction.POSITIVE, labels=("Dips", "Rest"))
    assert empty.outcome is Outcome.NO_DATA and "no complete forward windows" in empty.finding


def test_mean_test_finding_text():
    assert mean_test_finding(mean_test([]), "gain") == "no dates to compare gain on"
    assert mean_test_finding(mean_test([0.1, 0.3]), "gain").startswith("gain: mean +0.200 over 2 dates")
