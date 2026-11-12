"""Marks tests — the screening thresholds must sit where the scoring curves say they do."""

from __future__ import annotations

import pytest

from data_sources.fundamentals_source import BalanceSheetSnapshot
from data_sources.universe_source import Measure
from scorers.fundamentals_scorer import bank, industrial
from scorers.fundamentals_scorer.marks import INDUSTRIAL_MARKS, LENDER_MARKS, Mark

# Mark → the snapshot field its curve reads. Growth is checked separately:
# its marks are the veto line and the pace the curve scores 70, not 80 and 100.
_FIELDS: dict[Measure, str] = {
    Measure.RETURN_ON_EQUITY: "roe",
    Measure.RETURN_ON_CAPITAL: "roce",
    Measure.RETURN_ON_ASSETS: "roa",
    Measure.DEBT_TO_EQUITY: "debt_to_equity",
    Measure.CURRENT_RATIO: "current_ratio",
    Measure.QUICK_RATIO: "quick_ratio",
}
_GROWTH = (Measure.REVENUE_GROWTH, Measure.PROFIT_GROWTH)


def _curve_score(rubric, mark: Mark, value: float) -> float:
    """What the rubric's own curve gives this one input, alone on a snapshot."""
    snap = BalanceSheetSnapshot(ticker="T", as_of_date="2026-11-12", **{_FIELDS[mark.measure]: value})
    scorer = (
        rubric._score_profitability
        if mark.measure in (Measure.RETURN_ON_EQUITY, Measure.RETURN_ON_CAPITAL, Measure.RETURN_ON_ASSETS)
        else rubric._score_balance_sheet_health
    )
    score, _ = scorer(snap)
    assert score is not None
    return score


@pytest.mark.parametrize("mark", [m for m in INDUSTRIAL_MARKS if m.measure not in _GROWTH],
                         ids=lambda m: m.measure.value)
def test_industrial_marks_sit_at_80_and_at_the_top_of_their_curves(mark: Mark):
    assert 80 <= _curve_score(industrial, mark, mark.good) <= 90
    assert _curve_score(industrial, mark, mark.excellent) == 100
    # A hair on the wrong side of `good` is below 80: the mark is the curve's own breakpoint.
    worse = mark.good * (0.99 if mark.higher_is_better else 1.01)
    assert _curve_score(industrial, mark, worse) < _curve_score(industrial, mark, mark.good)


@pytest.mark.parametrize("mark", [m for m in LENDER_MARKS if m.measure not in _GROWTH],
                         ids=lambda m: m.measure.value)
def test_lender_marks_sit_on_the_bank_curves(mark: Mark):
    assert 80 <= _curve_score(bank, mark, mark.good) <= 90
    assert _curve_score(bank, mark, mark.excellent) == 100


def test_growth_marks_are_the_veto_line_and_the_strong_growth_pace():
    for mark in (m for m in INDUSTRIAL_MARKS if m.measure in _GROWTH):
        slow = BalanceSheetSnapshot(ticker="T", as_of_date="2026-11-12",
                                    rev_3y_cagr=mark.good - 0.1, pat_3y_cagr=mark.good - 0.1)
        clear = BalanceSheetSnapshot(ticker="T", as_of_date="2026-11-12",
                                     rev_3y_cagr=mark.good, pat_3y_cagr=mark.good)
        assert industrial.growth_deceleration_cap(slow) is not None  # just under: vetoed
        assert industrial.growth_deceleration_cap(clear) is None  # at the mark: clear of it
        strong = BalanceSheetSnapshot(ticker="T", as_of_date="2026-11-12", rev_3y_cagr=mark.excellent)
        assert industrial._score_growth(strong)[0] == 70.0


def test_weights_are_the_rubrics_shares():
    by_measure = {mark.measure: mark.weight for mark in INDUSTRIAL_MARKS}
    returns = (Measure.RETURN_ON_EQUITY, Measure.RETURN_ON_CAPITAL, Measure.RETURN_ON_ASSETS)
    assert sum(by_measure[m] for m in returns) == pytest.approx(20)  # profitability
    assert by_measure[Measure.DEBT_TO_EQUITY] == pytest.approx(35 * 0.30)  # of balance-sheet health
    assert by_measure[Measure.REVENUE_GROWTH] + by_measure[Measure.PROFIT_GROWTH] == pytest.approx(15)
    assert {mark.measure for mark in LENDER_MARKS} == {
        Measure.RETURN_ON_EQUITY, Measure.RETURN_ON_ASSETS, Measure.PROFIT_GROWTH,
    }
