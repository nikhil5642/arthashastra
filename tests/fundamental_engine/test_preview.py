"""Preview tests — the arithmetic of 'how many of the rubric's marks does it clear'."""

from __future__ import annotations

import pytest

from data_sources import universe_source
from data_sources.universe_source import Bound, Criteria, Measure
from fundamental_engine.preview import Preview, preview
from scorers.fundamentals_scorer.marks import Mark

ROE = Mark(Measure.RETURN_ON_EQUITY, weight=20.0, good=15.0, excellent=20.0)
DEBT = Mark(Measure.DEBT_TO_EQUITY, weight=30.0, good=0.5, excellent=0.3, higher_is_better=False)
CRITERIA = Criteria(markets=("us",))

# What Yahoo "knows" about each listing; a missing ratio is one it has no value for.
RATIOS: dict[str, dict[Measure, float]] = {
    "BEST": {Measure.RETURN_ON_EQUITY: 31.0, Measure.DEBT_TO_EQUITY: 0.1},
    "GOOD": {Measure.RETURN_ON_EQUITY: 16.0, Measure.DEBT_TO_EQUITY: 0.4},
    "MIXED": {Measure.RETURN_ON_EQUITY: 25.0, Measure.DEBT_TO_EQUITY: 1.9},
    "DEBT_FREE_UNKNOWN_ROE": {Measure.DEBT_TO_EQUITY: 0.0},
    "NEGATIVE_EQUITY": {Measure.RETURN_ON_EQUITY: 40.0, Measure.DEBT_TO_EQUITY: -3.0},
    "UNKNOWN": {},
}


@pytest.fixture
def asked(monkeypatch) -> list[Bound]:
    """Answer each question from RATIOS, and record what was asked."""
    questions: list[Bound] = []

    async def fake_fetch_symbols(criteria: Criteria) -> set[str]:
        bound = criteria.bounds[-1]
        questions.append(bound)
        passing = set()
        for symbol, ratios in RATIOS.items():
            value = ratios.get(bound.measure)
            if value is None:
                continue
            # A ceiling is read from zero up: negative equity is not "low debt".
            floor = bound.at_least if bound.at_least is not None else (0.0 if bound.at_most is not None else None)
            if (floor is None or value >= floor) and (bound.at_most is None or value <= bound.at_most):
                passing.add(symbol)
        return passing

    monkeypatch.setattr(universe_source, "fetch_symbols", fake_fetch_symbols)
    return questions


async def test_a_preview_is_the_weighted_share_of_marks_cleared(asked):
    previews = await preview(CRITERIA, set(RATIOS), [ROE, DEBT])
    assert previews is not None
    assert {symbol: p.score for symbol, p in previews.items()} == {
        "BEST": 100.0,  # excellent on both
        "GOOD": 50.0,  # good on both: half of each mark's weight
        "MIXED": 40.0,  # all 20 of ROE's points, none of debt's 30
        "DEBT_FREE_UNKNOWN_ROE": 100.0,  # judged on the one ratio Yahoo knows — an unknown is not a miss
        "NEGATIVE_EQUITY": 40.0,  # known to be levered beyond measure: no points for "low" debt
    }
    assert "UNKNOWN" not in previews  # nothing known, nothing claimed
    # …but it is not a pass either: how much the score rests on is part of it.
    assert previews["BEST"] == Preview(score=100.0, coverage=1.0) and not previews["BEST"].thin
    assert previews["DEBT_FREE_UNKNOWN_ROE"] == Preview(score=100.0, coverage=0.6)
    assert not previews["DEBT_FREE_UNKNOWN_ROE"].thin  # debt is 30 of the 50 points: enough to rank on
    assert Preview(score=100.0, coverage=0.4).thin

    # Three questions a mark: is it known, does it clear good, does it clear excellent.
    assert asked == [
        Bound(Measure.RETURN_ON_EQUITY),
        Bound(Measure.RETURN_ON_EQUITY, at_least=15.0),
        Bound(Measure.RETURN_ON_EQUITY, at_least=20.0),
        Bound(Measure.DEBT_TO_EQUITY),
        Bound(Measure.DEBT_TO_EQUITY, at_most=0.5),
        Bound(Measure.DEBT_TO_EQUITY, at_most=0.3),
    ]


async def test_only_the_symbols_asked_about_are_previewed(asked):
    assert await preview(CRITERIA, {"GOOD"}, [ROE]) == {"GOOD": Preview(score=50.0, coverage=1.0)}
    assert await preview(CRITERIA, set(), [ROE]) == {}


async def test_a_question_yahoo_does_not_answer_voids_the_whole_preview(monkeypatch):
    calls = 0

    async def flaky(criteria: Criteria) -> set[str] | None:
        nonlocal calls
        calls += 1
        return None if calls == 2 else {"BEST"}

    monkeypatch.setattr(universe_source, "fetch_symbols", flaky)
    # Half a preview would rank companies on whichever marks happened to load.
    assert await preview(CRITERIA, {"BEST"}, [ROE, DEBT]) is None
