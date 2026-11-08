"""Every band of every scoring curve — inputs on each side of each threshold."""

from __future__ import annotations

import pytest

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.fundamentals_scorer import bank, industrial, ownership


def _snap(**fields) -> BalanceSheetSnapshot:
    return BalanceSheetSnapshot(ticker="TEST", as_of_date="2026-11-08", **fields)


@pytest.mark.parametrize("field,value,expected", [
    ("debt_to_equity", 0.2, 100.0), ("debt_to_equity", 0.4, 85.0), ("debt_to_equity", 0.75, 65.0),
    ("debt_to_equity", 1.25, 37.5), ("debt_to_equity", 3.0, 0.0),
    ("interest_coverage", 12.0, 100.0), ("interest_coverage", 8.0, 92.5),
    ("interest_coverage", 4.0, 65.0), ("interest_coverage", 2.0, 27.5), ("interest_coverage", 0.5, 0.0),
    ("current_ratio", 2.5, 100.0), ("current_ratio", 1.75, 92.5), ("current_ratio", 1.25, 70.0),
    ("current_ratio", 0.85, 40.0), ("current_ratio", 0.35, 12.5),
    ("quick_ratio", 2.0, 100.0), ("quick_ratio", 1.25, 90.0), ("quick_ratio", 0.85, 67.5),
    ("quick_ratio", 0.55, 40.0), ("quick_ratio", 0.2, 12.5),
    ("net_debt_to_ebitda", -1.0, 100.0), ("net_debt_to_ebitda", 0.5, 85.0),
    ("net_debt_to_ebitda", 1.75, 65.0), ("net_debt_to_ebitda", 3.25, 35.0), ("net_debt_to_ebitda", 5.0, 10.0),
])
def test_balance_sheet_curves(field, value, expected):
    score, _ = industrial._score_balance_sheet_health(_snap(**{field: value}))
    assert score == pytest.approx(expected)


@pytest.mark.parametrize("roe,roa,expected", [
    (25.0, 15.0, 100.0), (17.5, 10.0, 90.0), (12.5, 6.5, 66.25), (7.5, 3.0, 35.0), (2.0, 1.0, 10.25),
])
def test_profitability_curves(roe, roa, expected):
    score, _ = industrial._score_profitability(_snap(roe=roe, roa=roa))
    assert score == pytest.approx(expected)


@pytest.mark.parametrize("cagr,expected", [(25.0, 100.0), (16.0, 85.0), (9.0, 55.0), (3.0, 20.0), (-4.0, 0.0)])
def test_growth_curve(cagr, expected):
    score, _ = industrial._score_growth(_snap(rev_3y_cagr=cagr))
    assert score == pytest.approx(expected)


@pytest.mark.parametrize("ocf,fcf,expected", [
    (1.5, 1.2, 100.0), (1.0, 0.8, 86.25), (0.65, 0.45, 55.0), (0.3, 0.15, 20.25), (-0.2, -0.5, 0.0),
])
def test_earnings_quality_curves(ocf, fcf, expected):
    score, _ = industrial._score_earnings_quality(_snap(ocf_to_pat=ocf, fcf_to_pat=fcf))
    assert score == pytest.approx(expected)


@pytest.mark.parametrize("pledge,expected", [(0.0, 100.0), (3.0, 80.0), (8.0, 50.0), (20.0, 20.0), (40.0, 0.0)])
def test_pledge_bands(pledge, expected):
    assert ownership.score(_snap(pledge_pct=pledge))[0] == expected


@pytest.mark.parametrize("delta,expected", [(4.0, 95.0), (1.5, 85.0), (0.0, 65.0), (-2.0, 40.0), (-5.0, 15.0)])
def test_insider_change_bands(delta, expected):
    assert ownership.score(_snap(insider_delta_1y_pct_points=delta))[0] == expected


@pytest.mark.parametrize("holding,expected", [(60.0, 85.0), (30.0, 75.0), (5.0, 65.0)])
def test_holding_bands(holding, expected):
    assert ownership.score(_snap(insider_holding=holding))[0] == expected


def test_ownership_without_data_is_dropped():
    assert ownership.score(_snap()) == (None, [])


@pytest.mark.parametrize("roe,roa,nim,expected", [
    (20.0, 2.0, 4.5, 100.0), (16.5, 1.6, 3.75, 90.0), (13.5, 1.35, 3.25, 70.0),
    (10.0, 1.0, 2.75, 43.61), (5.0, 0.7, 2.0, 22.17), (3.0, 0.3, 1.0, 10.0),
])
def test_bank_profitability_curves(roe, roa, nim, expected):
    score, _ = bank._score_profitability(_snap(roe=roe, roa=roa, nim=nim))
    assert score == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("cagr,expected", [(20.0, 100.0), (15.0, 85.0), (10.0, 57.5), (6.0, 32.5), (2.0, 10.0), (-3.0, 0.0)])
def test_bank_growth_curve(cagr, expected):
    assert bank._score_growth(_snap(pat_3y_cagr=cagr))[0] == pytest.approx(expected)


@pytest.mark.parametrize("ratio,cd,expected", [
    (40.0, 75.0, 100.0), (30.0, 85.0, 83.75), (20.0, 95.0, 61.07),
    (12.0, 105.0, 35.63), (5.0, 120.0, 6.25),
])
def test_bank_franchise_curves(ratio, cd, expected):
    assert bank._score_franchise(
        _snap(non_interest_income_ratio=ratio, credit_deposit_ratio=cd)
    )[0] == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("gross,net,delta,expected", [
    (0.8, 0.2, -0.6, 95.0), (1.5, 0.5, 0.0, 83.7), (2.8, 1.2, 0.5, 57.22),
    (5.0, 2.0, 1.2, 30.67), (8.0, 4.0, None, 15.0), (12.0, 6.0, 2.0, 3.33),
])
def test_bank_asset_quality_curves(gross, net, delta, expected):
    score, _ = bank._score_asset_quality(
        _snap(gross_npa_pct=gross, net_npa_pct=net, gross_npa_delta_1y_pct_points=delta)
    )
    assert score == pytest.approx(expected, abs=0.01)


def test_bank_with_no_inputs_returns_neutral_prior():
    result = bank.score(_snap(is_bank=True))
    assert (result.score, result.confidence) == (50.0, 0.0)
