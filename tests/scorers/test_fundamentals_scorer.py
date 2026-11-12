"""Deterministic fundamentals scorer tests — all inputs constructed inline."""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.fundamentals_scorer import score_fundamentals


def _snap(**fields) -> BalanceSheetSnapshot:
    defaults = {
        "ticker": "TEST",
        "as_of_date": "2026-04-11",
    }
    defaults.update(fields)
    return BalanceSheetSnapshot(**defaults)


def test_perfect_fundamentals_scores_high():
    snap = _snap(
        debt_to_equity=0.2,
        interest_coverage=15.0,
        current_ratio=2.2,
        quick_ratio=1.6,
        net_debt_to_ebitda=0.5,
        roe=25.0,
        roce=28.0,
        roa=14.0,
        rev_3y_cagr=22.0,
        pat_3y_cagr=20.0,
        ocf_to_pat=1.25,
        fcf_to_pat=1.0,
        insider_holding=65.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    assert result.pillar == "fundamentals"
    assert result.score >= 90
    assert result.data_completeness_pct == 100.0
    assert result.confidence >= 0.99
    assert len(result.evidence) >= 10
    assert not result.low_confidence


def test_distressed_fundamentals_with_pledge_veto():
    snap = _snap(
        debt_to_equity=2.5,
        interest_coverage=0.8,
        current_ratio=0.6,
        quick_ratio=0.3,
        net_debt_to_ebitda=6.0,
        roe=2.0,
        roce=3.5,
        roa=1.0,
        rev_3y_cagr=-5.0,
        pat_3y_cagr=-12.0,
        ocf_to_pat=0.3,
        fcf_to_pat=-0.1,
        insider_holding=30.0,
        pledge_pct=45.0,
    )
    result = score_fundamentals(snap)
    assert result.score <= 40
    assert any("VETO" in ev for ev in result.evidence)
    assert result.data_completeness_pct == 100.0


def test_pledge_veto_caps_otherwise_good_score():
    snap = _snap(
        debt_to_equity=0.3,
        interest_coverage=10.0,
        roe=22.0,
        roce=25.0,
        rev_3y_cagr=18.0,
        pat_3y_cagr=15.0,
        ocf_to_pat=1.1,
        insider_holding=55.0,
        pledge_pct=35.0,
    )
    result = score_fundamentals(snap)
    assert result.score == 40.0
    assert any("pledge" in ev.lower() and "VETO" in ev for ev in result.evidence)


def test_sparse_inputs_degrade_gracefully():
    snap = _snap(debt_to_equity=0.5, roe=15.0)
    result = score_fundamentals(snap)
    assert 0 < result.data_completeness_pct < 100
    assert result.low_confidence is True
    assert 30 <= result.score <= 100


def test_empty_inputs_return_neutral_prior():
    snap = _snap()
    result = score_fundamentals(snap)
    assert result.score == 50.0
    assert result.confidence == 0.0
    assert result.data_completeness_pct == 0.0


def test_evidence_mentions_every_provided_component():
    snap = _snap(
        debt_to_equity=0.5,
        interest_coverage=8.0,
        current_ratio=1.8,
        quick_ratio=1.2,
        net_debt_to_ebitda=1.0,
        roe=20.0,
        roce=24.0,
        roa=11.0,
        rev_3y_cagr=15.0,
        pat_3y_cagr=12.0,
        ocf_to_pat=1.0,
        fcf_to_pat=0.7,
        insider_holding=55.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    body = "\n".join(result.evidence)
    assert "D/E" in body
    assert "Interest coverage" in body
    assert "Current ratio" in body
    assert "Quick ratio" in body
    assert "Net debt / EBITDA" in body
    assert "ROE" in body
    assert "ROCE" in body
    assert "ROA" in body
    assert "Revenue 3y CAGR" in body
    assert "PAT 3y CAGR" in body
    assert "OCF/PAT" in body
    assert "FCF/PAT" in body
    assert "Insider / promoter holding" in body
    assert "Pledged shares" in body


# ── Growth-deceleration veto ──────────────────────────────────────────────────


def _pristine_balance_sheet(
    *, rev: float, pat: float
) -> BalanceSheetSnapshot:
    """TCS-style pristine balance sheet with configurable growth rates."""
    return _snap(
        debt_to_equity=0.10,
        interest_coverage=54.0,
        current_ratio=2.4,
        quick_ratio=2.1,
        net_debt_to_ebitda=-0.2,
        roe=52.0,
        roce=63.0,
        roa=30.0,
        rev_3y_cagr=rev,
        pat_3y_cagr=pat,
        ocf_to_pat=1.06,
        fcf_to_pat=0.95,
        insider_holding=72.0,
        pledge_pct=0.0,
    )


def test_growth_cap_70_when_both_cagrs_under_7():
    """TCS-like: 5.8% rev / 5.3% PAT — pristine balance sheet, score capped at 70."""
    snap = _pristine_balance_sheet(rev=5.8, pat=5.3)
    result = score_fundamentals(snap)
    assert result.score == 70.0
    assert any(
        "3y growth decelerating" in ev and "< 7" in ev
        for ev in result.evidence
    )


def test_growth_cap_60_when_both_cagrs_under_5():
    snap = _pristine_balance_sheet(rev=4.0, pat=3.8)
    result = score_fundamentals(snap)
    assert result.score == 60.0
    assert any("< 5" in ev for ev in result.evidence)


def test_growth_cap_50_when_both_cagrs_under_3():
    snap = _pristine_balance_sheet(rev=2.0, pat=1.5)
    result = score_fundamentals(snap)
    assert result.score == 50.0
    assert any("< 3" in ev for ev in result.evidence)


def test_no_growth_cap_when_only_one_cagr_low():
    """One weak, one strong — no cap fires (typical mid-cycle recovery)."""
    snap = _pristine_balance_sheet(rev=4.0, pat=15.0)
    result = score_fundamentals(snap)
    assert result.score > 80  # uncapped, high because of quality
    assert not any("growth decelerating" in ev for ev in result.evidence)


def test_no_growth_cap_when_cagrs_missing():
    """Sparse data — the cap doesn't trigger when either CAGR is None."""
    snap = _snap(
        debt_to_equity=0.10,
        interest_coverage=54.0,
        roe=52.0,
        roce=63.0,
        # no rev/pat CAGRs
        ocf_to_pat=1.06,
        insider_holding=72.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    assert not any("growth decelerating" in ev for ev in result.evidence)


def test_growth_cap_respects_already_low_scores():
    """If the uncapped score is already below the cap, the cap is a no-op."""
    # Weak balance sheet + slow growth — uncapped score already ~40.
    snap = _snap(
        debt_to_equity=2.0,
        interest_coverage=1.5,
        roe=3.0,
        roce=4.0,
        rev_3y_cagr=5.0,
        pat_3y_cagr=4.0,
        ocf_to_pat=0.5,
        insider_holding=30.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    # Score stays where it was — no "capped at X" evidence bullet.
    assert result.score < 50


# ── New balance-sheet ratio coverage ──────────────────────────────────────────


def test_net_cash_balance_sheet_scores_higher_than_levered():
    """Net-cash business beats an identical levered business."""
    levered = _snap(
        debt_to_equity=1.2,
        interest_coverage=4.0,
        current_ratio=1.3,
        quick_ratio=0.9,
        net_debt_to_ebitda=3.0,
        roe=18.0,
        roce=20.0,
        roa=8.0,
        rev_3y_cagr=14.0,
        pat_3y_cagr=13.0,
        ocf_to_pat=0.9,
        fcf_to_pat=0.4,
        insider_holding=55.0,
        pledge_pct=0.0,
    )
    net_cash = _snap(
        debt_to_equity=0.1,
        interest_coverage=50.0,
        current_ratio=2.5,
        quick_ratio=2.2,
        net_debt_to_ebitda=-0.5,
        roe=18.0,
        roce=20.0,
        roa=8.0,
        rev_3y_cagr=14.0,
        pat_3y_cagr=13.0,
        ocf_to_pat=0.9,
        fcf_to_pat=0.4,
        insider_holding=55.0,
        pledge_pct=0.0,
    )
    assert score_fundamentals(net_cash).score > score_fundamentals(levered).score + 10


def test_liquidity_crunch_drags_balance_sheet():
    """A business with D/E 0.4 but current_ratio 0.5 is still fragile."""
    snap = _snap(
        debt_to_equity=0.4,
        interest_coverage=8.0,
        current_ratio=0.5,
        quick_ratio=0.2,
        net_debt_to_ebitda=2.0,
        roe=18.0,
        roce=20.0,
        roa=9.0,
        rev_3y_cagr=12.0,
        pat_3y_cagr=10.0,
        ocf_to_pat=0.9,
        fcf_to_pat=0.5,
        insider_holding=55.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    # Liquidity drag means we should not hit >80 even with 20%+ ROE.
    assert result.score < 80


def test_bank_snapshot_routes_to_bank_rubric():
    """Banks should not be penalised for D/E, low ROA, or zero promoter holding."""
    snap = _snap(
        sector="Financial Services",
        industry="Banks - Regional",
        is_bank=True,
        # Deliberately "bad" industrial metrics — all normal for a bank:
        debt_to_equity=1.11,
        roe=14.4,
        roa=1.63,
        roce=7.5,
        nim=3.38,
        pat_3y_cagr=24.4,
        non_interest_income_ratio=46.9,
        credit_deposit_ratio=116.1,
        insider_holding=0.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    # Bank rubric applied — should score much higher than industrial curve.
    assert result.score > 70
    assert any("Bank rubric" in ev for ev in result.evidence)
    assert any("NIM" in ev for ev in result.evidence)


def test_bank_rubric_differentiates_strong_vs_weak_bank():
    """A bank with elite metrics should score meaningfully higher than a weak one."""
    strong = _snap(
        sector="Financial Services",
        industry="Banks",
        is_bank=True,
        roe=18.0,
        roa=2.0,
        nim=4.0,
        pat_3y_cagr=20.0,
        non_interest_income_ratio=40.0,
        credit_deposit_ratio=85.0,
        insider_holding=0.0,
        pledge_pct=0.0,
    )
    weak = _snap(
        sector="Financial Services",
        industry="Banks",
        is_bank=True,
        roe=10.0,
        roa=0.7,
        nim=2.2,
        pat_3y_cagr=5.0,
        non_interest_income_ratio=12.0,
        credit_deposit_ratio=115.0,
        insider_holding=0.0,
        pledge_pct=0.0,
    )
    assert score_fundamentals(strong).score > score_fundamentals(weak).score + 30


def test_bank_rubric_penalises_high_credit_deposit_ratio():
    """CD ratio > 100% (lending > funding) drags the franchise sub-score."""
    healthy = _snap(
        sector="Financial Services",
        industry="Banks",
        is_bank=True,
        roe=15.0, roa=1.5, nim=3.5, pat_3y_cagr=15.0,
        non_interest_income_ratio=30.0,
        credit_deposit_ratio=85.0,
        insider_holding=0.0, pledge_pct=0.0,
    )
    stressed = _snap(
        sector="Financial Services",
        industry="Banks",
        is_bank=True,
        roe=15.0, roa=1.5, nim=3.5, pat_3y_cagr=15.0,
        non_interest_income_ratio=30.0,
        credit_deposit_ratio=118.0,  # stressed
        insider_holding=0.0, pledge_pct=0.0,
    )
    assert score_fundamentals(healthy).score > score_fundamentals(stressed).score + 5


def test_bank_dispatch_by_sector_string_when_flag_missing():
    """Legacy snapshots without is_bank still route via sector/industry string."""
    snap = _snap(
        sector="Financial Services",
        industry="Banks - Private",
        roe=16.0, roa=1.7, nim=3.4, pat_3y_cagr=15.0,
        non_interest_income_ratio=35.0,
        credit_deposit_ratio=90.0,
        insider_holding=0.0, pledge_pct=0.0,
    )
    # Not set: is_bank = False (default). Should still route via sector match.
    result = score_fundamentals(snap)
    assert any("Bank rubric" in ev for ev in result.evidence)


def test_fcf_weaker_than_ocf_tempers_earnings_quality():
    """Capex-heavy business with OCF conversion but poor FCF sees a dent."""
    heavy_capex = _snap(
        debt_to_equity=0.3,
        interest_coverage=10.0,
        current_ratio=1.8,
        quick_ratio=1.3,
        net_debt_to_ebitda=0.8,
        roe=20.0,
        roce=22.0,
        roa=10.0,
        rev_3y_cagr=15.0,
        pat_3y_cagr=14.0,
        ocf_to_pat=1.1,
        fcf_to_pat=0.1,  # nearly all reinvested
        insider_holding=55.0,
        pledge_pct=0.0,
    )
    light_capex = _snap(
        debt_to_equity=0.3,
        interest_coverage=10.0,
        current_ratio=1.8,
        quick_ratio=1.3,
        net_debt_to_ebitda=0.8,
        roe=20.0,
        roce=22.0,
        roa=10.0,
        rev_3y_cagr=15.0,
        pat_3y_cagr=14.0,
        ocf_to_pat=1.1,
        fcf_to_pat=0.95,
        insider_holding=55.0,
        pledge_pct=0.0,
    )
    assert (
        score_fundamentals(light_capex).score
        > score_fundamentals(heavy_capex).score
    )


# ── Ownership sub-score (shared by both rubrics) ──────────────────────────────


def _quality_snap(**overrides) -> BalanceSheetSnapshot:
    fields = dict(
        debt_to_equity=0.2, interest_coverage=15.0, current_ratio=2.0,
        roe=22.0, roce=25.0, rev_3y_cagr=14.0, pat_3y_cagr=15.0, ocf_to_pat=1.1,
    )
    fields.update(overrides)
    return _snap(**fields)


def test_widely_held_company_is_not_penalised_for_low_insider_stake():
    """Infosys / Apple style ownership is normal, not a fundamentals defect."""
    widely_held = score_fundamentals(_quality_snap(insider_holding=2.0))
    controlled = score_fundamentals(_quality_snap(insider_holding=60.0))
    assert controlled.score - widely_held.score < 4
    assert any("widely held" in ev for ev in widely_held.evidence)


def test_unknown_pledge_is_not_scored_as_clean():
    result = score_fundamentals(_quality_snap(insider_holding=55.0, pledge_pct=None))
    assert not any("Pledged shares" in ev for ev in result.evidence)


def test_insider_selling_drags_the_ownership_score():
    steady = score_fundamentals(
        _quality_snap(insider_holding=55.0, insider_delta_1y_pct_points=0.0)
    )
    selling = score_fundamentals(
        _quality_snap(insider_holding=55.0, insider_delta_1y_pct_points=-4.0)
    )
    assert selling.score < steady.score
    assert any("heavy insider selling" in ev for ev in selling.evidence)


# ── Rubric routing ────────────────────────────────────────────────────────────


def test_asset_light_financial_uses_the_industrial_rubric():
    """Payment networks, exchanges, AMCs: financial sector, ordinary P&L."""
    snap = _quality_snap(
        sector="Financial Services", industry="Credit Services", current_ratio=1.4
    )
    result = score_fundamentals(snap)
    assert not any("Bank rubric" in ev for ev in result.evidence)
    assert any("D/E" in ev for ev in result.evidence)


def test_insurer_without_a_current_ratio_split_uses_the_bank_rubric():
    snap = _snap(
        sector="Financial Services", industry="Insurance - Life",
        roe=16.0, roa=1.4, pat_3y_cagr=14.0,
    )
    result = score_fundamentals(snap)
    assert any("bank rubric" in ev.lower() for ev in result.evidence)


# ── Bank asset quality ────────────────────────────────────────────────────────


def _bank(**overrides) -> BalanceSheetSnapshot:
    fields = dict(
        sector="Financial Services", industry="Banks", is_bank=True,
        roe=16.0, roa=1.6, nim=3.6, pat_3y_cagr=15.0,
        non_interest_income_ratio=30.0, credit_deposit_ratio=85.0,
    )
    fields.update(overrides)
    return _snap(**fields)


def test_bank_asset_quality_separates_clean_and_stressed_books():
    clean = score_fundamentals(
        _bank(gross_npa_pct=1.2, net_npa_pct=0.3, gross_npa_delta_1y_pct_points=-0.3)
    )
    stressed = score_fundamentals(
        _bank(gross_npa_pct=7.0, net_npa_pct=3.5, gross_npa_delta_1y_pct_points=1.5)
    )
    assert clean.score > stressed.score + 15
    assert any("Gross NPA" in ev for ev in stressed.evidence)


def test_bank_without_npa_data_says_asset_quality_is_unscored():
    result = score_fundamentals(_bank())
    assert any("asset quality is unscored" in ev for ev in result.evidence)


# ── Global scenarios ──────────────────────────────────────────────────────────


def test_loss_making_recent_ipo_scores_low_without_crashing():
    """Rubrik-style: growing fast, losing money, core EBIT below interest cost."""
    snap = _snap(
        sector="Technology", industry="Software - Infrastructure",
        debt_to_equity=1.6, interest_coverage=-4.2, current_ratio=1.1,
        roe=-55.0, roa=-12.0, rev_3y_cagr=38.0, pat_3y_cagr=None,
        ocf_to_pat=None, insider_holding=9.0,
    )
    result = score_fundamentals(snap)
    assert result.score < 45
    assert any("Interest coverage = -4.20× → 0/100" in ev for ev in result.evidence)
    assert not any("growth decelerating" in ev for ev in result.evidence)


def test_us_bank_with_yahoo_only_data_uses_bank_rubric():
    """JPMorgan-style: no screener tables, no current-ratio split, no NPA data."""
    snap = _snap(
        sector="Financial Services", industry="Banks - Diversified",
        roe=16.5, roa=1.3, pat_3y_cagr=11.0, insider_holding=0.5,
    )
    result = score_fundamentals(snap)
    assert any("Bank rubric" in ev for ev in result.evidence)
    assert any("asset quality is unscored" in ev for ev in result.evidence)


def test_mature_global_compounder_gets_the_same_curves_as_anyone():
    """No brand priors: a Nestlé-like shrinking business is capped like any other."""
    snap = _snap(
        sector="Consumer Defensive", debt_to_equity=1.2, interest_coverage=12.0,
        roe=30.0, roce=18.0, roa=9.0, rev_3y_cagr=-1.5, pat_3y_cagr=-4.0,
        ocf_to_pat=1.3, fcf_to_pat=0.9, insider_holding=0.1,
    )
    result = score_fundamentals(snap)
    assert result.score == 50.0
    assert any("< 3%" in ev for ev in result.evidence)


# ── Known-bad is never scored as unknown ─────────────────────────────────────


def test_negative_equity_scores_zero_on_leverage_instead_of_dropping_out():
    healthy_rest = dict(interest_coverage=12.0, current_ratio=2.2, quick_ratio=1.6)
    unknown_equity = score_fundamentals(_snap(**healthy_rest))
    negative_equity = score_fundamentals(_snap(**healthy_rest, shareholders_equity=-500.0))

    assert unknown_equity.score == 100.0  # D/E simply missing
    # D/E axis (30 of 85 balance-sheet weight) now counts as 0.
    assert negative_equity.score == round(100 * 55 / 85, 1)
    assert any("Shareholders' equity is negative" in line for line in negative_equity.evidence)


def test_roe_of_a_negative_equity_company_is_explained_not_scored():
    result = score_fundamentals(_snap(roce=12.0, shareholders_equity=-1.0))
    assert any(line.startswith("ROE not meaningful") for line in result.evidence)


def test_profit_that_turned_into_a_loss_fires_the_growth_veto():
    """PAT CAGR −100 is 'all of it gone' — with weak revenue the cap must apply."""
    strong_balance_sheet = dict(debt_to_equity=0.1, interest_coverage=20.0, current_ratio=2.5,
                                quick_ratio=2.0, roe=22.0, roce=25.0, ocf_to_pat=1.3)
    collapsed = score_fundamentals(_snap(**strong_balance_sheet, rev_3y_cagr=1.5, pat_3y_cagr=-100.0))
    assert collapsed.score == 50.0
    assert any(line.startswith("VETO") and "PAT turned to a loss" in line for line in collapsed.evidence)
    assert any(line == "PAT over 3 years: profit turned into a loss → 0/100" for line in collapsed.evidence)

    # Unknown PAT growth (a loss that became a profit) still can't fire the veto.
    unknown = score_fundamentals(_snap(**strong_balance_sheet, rev_3y_cagr=1.5))
    assert unknown.score > 50.0


def test_components_left_out_for_lack_of_data_are_named():
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from scorers.fundamentals_scorer import score_fundamentals

    thin = BalanceSheetSnapshot(ticker="THIN", as_of_date="2026-11-12", roe=22.0, roce=25.0)
    line = next(e for e in score_fundamentals(thin).evidence if e.startswith("Not scored"))
    assert "growth" in line and "earnings quality" in line and "profitability" not in line



def test_an_insurer_is_told_which_bank_axes_do_not_apply_not_that_data_is_missing():
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from scorers.fundamentals_scorer import score_fundamentals

    reinsurer = BalanceSheetSnapshot(
        ticker="HNR1.DE", as_of_date="2026-11-13", sector="Financial Services",
        industry="Insurance - Reinsurance", roe=20.4, roa=3.7, pat_3y_cagr=12.0,
    )
    result = score_fundamentals(reinsurer)
    text = " | ".join(result.evidence)
    assert "Insurer, scored on the bank rubric" in text and "combined ratio" in text
    assert "Do not apply to an insurer (40% of the rubric): asset quality, franchise" in text
    assert "No GNPA" not in text
    # Governance had no ownership data: that one *is* missing, and says so.
    assert "Not scored for lack of data (10% of the rubric): governance" in text
    assert result.data_completeness_pct == 50.0  # inapplicable axes still count as not scored

    bank = BalanceSheetSnapshot(ticker="BANK", as_of_date="2026-11-13", sector="Financial Services",
                                industry="Banks - Regional", roe=15.0, roa=1.2)
    bank_text = " | ".join(score_fundamentals(bank).evidence)
    assert "No GNPA" in bank_text and "Not scored for lack of data" in bank_text
    assert "insurer" not in bank_text.lower()
