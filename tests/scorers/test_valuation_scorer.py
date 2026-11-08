"""Valuation pillar tests — every expected score worked out by hand from the curves."""

from __future__ import annotations

import pytest

from data_sources.fundamentals_source.snapshot import BalanceSheetSnapshot
from scorers.base import Pillar
from scorers.valuation_scorer import VALUE_TRAP_CAP, _interpolate, score_valuation


def snap(**overrides: object) -> BalanceSheetSnapshot:
    fields: dict[str, object] = {"ticker": "GOOGL", "as_of_date": "2026-11-08", "sector": "Technology"}
    fields.update(overrides)
    return BalanceSheetSnapshot(**fields)  # type: ignore[arg-type]


def test_industrial_blend_of_earnings_yield_fcf_yield_and_history():
    result = score_valuation(snap(
        pe_ratio=20.0, earnings_yield=5.0, net_income=100.0,  # EY 5% → 75
        fcf_yield=5.0,  # → 85
        pe_history=[16.0, 20.0, 24.0],  # today at its median → 60
    ))
    assert result.pillar is Pillar.VALUATION
    assert result.score == 74.0  # (75·35 + 85·35 + 60·30) / 100
    assert (result.confidence, result.data_completeness_pct) == (1.0, 100.0)
    assert any("3-year median 20.0" in line for line in result.evidence)


def test_loss_maker_scores_zero_on_earnings_yield():
    result = score_valuation(snap(net_income=-10.0, fcf_yield=5.0))
    assert result.score == 42.5  # (0·35 + 85·35) / 70
    assert result.data_completeness_pct == 70.0
    assert result.evidence[0].startswith("Loss-making")


def test_negative_fcf_yield_scores_zero():
    result = score_valuation(snap(pe_ratio=20.0, earnings_yield=5.0, fcf_yield=-2.0))
    assert result.score == 37.5  # (75·35 + 0·35) / 70


def test_banks_swap_fcf_yield_for_price_to_book_against_roe():
    bank = snap(
        ticker="HDFCBANK.NS", sector="Financial Services", is_bank=True,
        pe_ratio=12.5, earnings_yield=8.0,  # → 100
        roe=16.5, price_to_book=1.5,  # justified P/B 16.5 / 11 = 1.5 → ratio 1.0 → 70
        pe_history=[12.5, 12.5, 12.5],  # → 60
        fcf_yield=-40.0,  # a lender's "FCF" is ignored
    )
    result = score_valuation(bank)
    assert result.score == 77.5  # (100·35 + 70·35 + 60·30) / 100
    assert any("justified 1.50" in line for line in result.evidence)

    unprofitable = score_valuation(snap(is_bank=True, roe=-2.0, price_to_book=0.6))
    assert unprofitable.score == 0.0


def test_value_trap_guard_caps_cheap_slow_growers():
    cheap = dict(pe_ratio=10.0, earnings_yield=10.0, net_income=50.0, fcf_yield=8.0)  # → 100
    assert score_valuation(snap(**cheap)).score == 100.0

    shrinking = score_valuation(snap(**cheap, rev_3y_cagr=3.0, pat_3y_cagr=2.0))
    assert shrinking.score == VALUE_TRAP_CAP
    assert any(line.startswith("CAP: value-trap guard") and "below 5%" in line for line in shrinking.evidence)

    growing = score_valuation(snap(**cheap, rev_3y_cagr=12.0, pat_3y_cagr=2.0))
    assert growing.score == 100.0  # one CAGR at 7%+ is not a slow grower


def test_no_data_is_neutral_with_zero_confidence():
    result = score_valuation(snap())
    assert (result.score, result.confidence, result.data_completeness_pct) == (50.0, 0.0, 0.0)
    assert "unscored" in result.evidence[0]


def test_history_needs_three_positive_points():
    assert score_valuation(snap(pe_ratio=20.0, pe_history=[18.0, 22.0])).data_completeness_pct == 0.0
    assert score_valuation(snap(pe_ratio=20.0, pe_history=[0.0, 0.0, 0.0])).data_completeness_pct == 0.0
    assert score_valuation(snap(pe_ratio=-5.0, pe_history=[18.0, 20.0, 22.0])).data_completeness_pct == 0.0


@pytest.mark.parametrize(
    ("x", "expected"),
    [(-1.0, 0.0), (0.5, 7.5), (1.0, 15.0), (5.0, 75.0), (8.0, 100.0), (12.0, 100.0)],
)
def test_earnings_yield_curve(x, expected):
    from scorers.valuation_scorer import _EARNINGS_YIELD_CURVE

    assert _interpolate(x, _EARNINGS_YIELD_CURVE) == pytest.approx(expected)


@pytest.mark.parametrize(("ratio", "expected"), [(0.5, 100.0), (0.85, 80.0), (1.1, 52.0), (1.6, 10.0), (3.0, 0.0)])
def test_vs_history_curve_rewards_a_discount_to_the_past(ratio, expected):
    from scorers.valuation_scorer import _VS_HISTORY_CURVE

    assert _interpolate(ratio, _VS_HISTORY_CURVE) == pytest.approx(expected)
