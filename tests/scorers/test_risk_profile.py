"""Risk-profile tests — risk is measured from a company's own tape and record, never assumed from size."""

from __future__ import annotations

import math

import pytest

from data_sources.fundamentals_source import BalanceSheetSnapshot
from data_sources.price_series import MIN_CLOSES_FOR_RISK, annualised_volatility_pct, worst_fall_pct
from scorers.risk_profile import MAX_RISK_DISCOUNT, RiskProfile, risk_discount, risk_profile


def test_volatility_is_the_yearly_swing_of_daily_returns():
    # Alternating +2% / −2% days: a daily spread of about 2%, about 32% over a year.
    closes = [100.0]
    for day in range(252):
        closes.append(closes[-1] * (1.02 if day % 2 == 0 else 0.98))
    assert annualised_volatility_pct(closes) == pytest.approx(0.0202 * math.sqrt(252) * 100, abs=0.5)
    assert annualised_volatility_pct([100.0] * 300) == 0.0  # a price that never moves
    assert annualised_volatility_pct(closes[: MIN_CLOSES_FOR_RISK - 1]) is None  # too short to say


def test_worst_fall_is_the_deepest_drop_from_a_high_to_a_later_low():
    climb = [100.0 + i for i in range(100)]  # to 199
    slide = [199.0 - 1.5 * i for i in range(1, 67)]  # down to 100
    recovery = [100.0 + i for i in range(1, 40)]
    assert worst_fall_pct(climb + slide + recovery) == pytest.approx((1 - 100 / 199) * 100, abs=0.1)
    assert worst_fall_pct([100.0 + i for i in range(200)]) == 0.0  # only ever rose
    assert worst_fall_pct(climb) is None


def test_a_profile_states_what_was_measured_and_leaves_the_rest_unknown():
    snap = BalanceSheetSnapshot(
        ticker="TINY.T", as_of_date="2026-11-12", currency="JPY", current_price=2474.0,
        market_cap=1.4e10, avg_daily_volume=12_000.0, volatility_1y_pct=68.0, worst_fall_3y_pct=57.0,
        price_history_years=9.6, net_margin_annual_trend=[-4.0, 6.0, 9.4, 16.8],
    )
    profile = risk_profile(snap)
    assert (profile.volatility_1y_pct, profile.worst_fall_3y_pct) == (68.0, 57.0)
    assert profile.avg_daily_traded_value == round(2474.0 * 12_000)  # ¥29.7M a day
    assert profile.daily_turnover_pct == pytest.approx(0.212, abs=0.001)  # of a ¥14B company
    assert (profile.fiscal_years, profile.loss_years) == (4, 1)
    assert RiskProfile.from_dict(profile.to_dict()) == profile

    bare = risk_profile(BalanceSheetSnapshot(ticker="NEW", as_of_date="2026-11-12"))
    assert bare == RiskProfile()  # nothing measured, nothing claimed — not "low risk"
    assert RiskProfile.from_dict("garbage") == RiskProfile()
    assert RiskProfile.from_dict({"volatility_1y_pct": "high", "fiscal_years": True}) == RiskProfile()


def test_pence_quotes_are_traded_in_pounds():
    shell = BalanceSheetSnapshot(ticker="SHEL.L", as_of_date="2026-11-12", currency="GBp",
                                 current_price=3722.5, avg_daily_volume=10_000_000.0, market_cap=2.11e11)
    assert risk_profile(shell).avg_daily_traded_value == round(37.225 * 10_000_000)


def test_the_risk_discount_grows_with_swings_losses_and_a_short_record():
    calm = RiskProfile(volatility_1y_pct=28.0, fiscal_years=10, loss_years=0)
    assert risk_discount(calm) == 0.0  # a calm, long, profitable record costs nothing
    assert risk_discount(RiskProfile(volatility_1y_pct=50.0, fiscal_years=10, loss_years=0)) == 10.0
    # Volatility is capped at 20 points; half the years in loss adds five.
    assert risk_discount(RiskProfile(volatility_1y_pct=140.0, fiscal_years=4, loss_years=2)) == 25.0
    # A short record is a risk of its own.
    assert risk_discount(RiskProfile(volatility_1y_pct=28.0, fiscal_years=2, loss_years=0)) == 5.0


def test_unmeasured_risk_is_discounted_not_waved_through():
    assert risk_discount(RiskProfile()) == 15.0  # no price history, no record: not "calm"
    worst = RiskProfile(volatility_1y_pct=200.0, fiscal_years=2, loss_years=2)
    assert risk_discount(worst) == MAX_RISK_DISCOUNT
