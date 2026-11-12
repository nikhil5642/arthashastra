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


# ── Sustainable earnings: one-offs out, cyclical peaks capped ────────────────


def test_unusual_gains_are_taken_out_of_the_earnings_the_price_is_set_against():
    """Alphabet-shaped: a P/E of 15 that is 27 once paper gains are removed."""
    result = score_valuation(snap(
        pe_ratio=15.0, earnings_yield=6.67, net_income=100.0,
        core_earnings_ratio_fy=0.556, pe_history=[22.0, 24.0, 26.0],
    ))
    core_yield = 6.67 * 0.556  # 3.71% → 40 + (3.71 − 2.5) × 25 / 1.5
    core_pe = 15.0 / 0.556  # 26.98 vs median 24 → 1.124× → 60 − 0.124 × 80
    expected = (
        (40 + (core_yield - 2.5) * 25 / 1.5) * 35 + (60 - (core_pe / 24 - 1) * 80) * 30
    ) / 65
    assert result.score == pytest.approx(expected, abs=0.05)
    assert result.evidence[0].startswith("ADJ: 44% of the profit behind the P/E is unusual items")
    assert "on core earnings = 3.71%" in result.evidence[1]
    assert "P/E 27.0 on core earnings vs its 3-year median 24.0" in result.evidence[2]


def test_one_off_charges_are_never_added_back():
    # Core above reported (a write-off depressed profit): the reported figure stands.
    result = score_valuation(snap(pe_ratio=20.0, earnings_yield=5.0, core_earnings_ratio_fy=1.3))
    assert result.score == 75.0
    assert not any(line.startswith("ADJ") for line in result.evidence)


def test_one_offs_under_five_percent_of_profit_are_noise():
    result = score_valuation(snap(pe_ratio=20.0, earnings_yield=5.0, core_earnings_ratio_fy=0.97))
    assert result.score == 75.0


def test_a_core_loss_behind_a_reported_profit_earns_no_yield():
    result = score_valuation(snap(
        pe_ratio=8.0, earnings_yield=12.5, core_earnings_ratio_fy=-0.2, pe_history=[10.0, 12.0, 14.0],
    ))
    assert result.score == 0.0
    assert result.data_completeness_pct == 35.0  # P/E-vs-history has nothing to compare
    assert "none of that profit is repeatable" in result.evidence[-1]


def test_margins_far_above_the_companys_own_median_count_as_cyclical():
    """Micron-shaped: 9× peak earnings, margin 2.9× its six-year median."""
    cyclical = snap(
        pe_ratio=9.0, earnings_yield=11.11, net_income=85.0, net_margin=63.8,
        net_margin_annual_trend=[21.2, 28.2, -37.5, 3.1, 22.8, 63.8],  # median 22.0
        fcf_yield=7.5,
    )
    result = score_valuation(cyclical)
    ratio = 1.5 * 22.0 / 63.8  # only 1.5× the median margin counts
    mid_cycle_yield = 11.11 * ratio  # 5.75% → 65 + (5.75 − 4) × 10
    assert result.score == pytest.approx(((65 + (mid_cycle_yield - 4) * 10) * 35 + 100 * 35) / 70, abs=0.05)
    assert "2.9× its 6-year median (22.0%)" in result.evidence[0]
    assert "on mid-cycle earnings" in result.evidence[1]
    assert any(line.startswith("FCF yield = 7.50%") and "→ 100/100" in line for line in result.evidence)


def test_a_margin_within_one_and_a_half_times_its_median_is_earned():
    steady = snap(pe_ratio=20.0, earnings_yield=5.0, net_margin=30.0,
                  net_margin_annual_trend=[22.0, 24.0, 26.0, 30.0])  # median 25 → allowance 37.5
    assert score_valuation(steady).score == 75.0


def test_a_business_that_typically_lost_money_has_no_proven_earnings():
    unproven = snap(pe_ratio=19.0, earnings_yield=5.26, net_margin=25.0, fcf_yield=3.5,
                    net_margin_annual_trend=[-24.0, -11.0, -37.0, 25.0])  # median −17.5
    result = score_valuation(unproven)
    fcf_score = 60 + (3.5 - 3.0) * 25 / 2
    assert result.score == pytest.approx((0 * 35 + fcf_score * 35) / 70, abs=0.05)
    assert "typically lost money" in result.evidence[0]


def test_the_cycle_test_needs_four_fiscal_years_and_skips_lenders():
    short = snap(pe_ratio=9.0, earnings_yield=11.11, net_margin=60.0,
                 net_margin_annual_trend=[5.0, 10.0, 60.0])
    assert score_valuation(short).score == 100.0  # three years: unknown, not adjusted

    bank = snap(is_bank=True, pe_ratio=9.0, earnings_yield=11.11, net_margin=60.0,
                net_margin_annual_trend=[5.0, 6.0, 7.0, 60.0])
    assert score_valuation(bank).score == 100.0  # a lender's margin on revenue is not operating


def test_loss_over_the_trailing_twelve_months_beats_a_profitable_fiscal_year():
    turned = snap(pe_ratio=20.0, earnings_yield=5.0, net_income=50.0, latest_annual_period="2025-12-31",
                  ttm_period_end="2026-09-30", ttm_net_income=-5.0)
    result = score_valuation(turned)
    assert result.score == 0.0
    assert result.evidence[0].startswith("Loss-making over the trailing twelve months")


def test_cash_flow_window_is_named_in_the_evidence():
    from data_sources.fundamentals_source.snapshot import StatementWindow

    result = score_valuation(snap(fcf_yield=0.57, cash_flow_window=StatementWindow.TTM))
    assert result.evidence == ["FCF yield = 0.57% over the trailing twelve months → 20/100"]


def test_a_quote_that_does_not_reconcile_is_left_unscored():
    receipt = snap(quote_unreliable=True, fcf_yield=9.0, pe_ratio=5.0, earnings_yield=20.0)
    result = score_valuation(receipt)
    assert (result.score, result.confidence) == (50.0, 0.0)
    assert "price and the statements don't reconcile" in result.evidence[0]


def test_a_price_under_five_times_repeatable_earnings_is_capped_not_rewarded():
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from scorers.valuation_scorer import VALUE_TRAP_CAP, score_valuation

    def valuation(pe: float):
        snap = BalanceSheetSnapshot(ticker="CHEAP", as_of_date="2026-11-13", pe_ratio=pe,
                                    earnings_yield=round(100 / pe, 2), fcf_yield=round(100 / pe, 2),
                                    rev_3y_cagr=20.0, pat_3y_cagr=25.0)
        return score_valuation(snap)

    doubted, cheap = valuation(2.6), valuation(8.0)
    assert doubted.score == VALUE_TRAP_CAP and any("priced for doubt" in e for e in doubted.evidence)
    assert cheap.score > VALUE_TRAP_CAP  # honestly cheap is still rewarded
    assert not any("priced for doubt" in e for e in cheap.evidence)
