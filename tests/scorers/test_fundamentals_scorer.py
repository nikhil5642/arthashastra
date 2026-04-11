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
        roe=25.0,
        roce=28.0,
        rev_3y_cagr=22.0,
        pat_3y_cagr=20.0,
        ocf_to_pat=1.25,
        promoter_holding=65.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    assert result.pillar == "fundamentals"
    assert result.score >= 90
    assert result.data_completeness_pct == 100.0
    assert result.confidence >= 0.99
    assert len(result.evidence) >= 5
    assert not result.low_confidence


def test_distressed_fundamentals_with_pledge_veto():
    snap = _snap(
        debt_to_equity=2.5,
        interest_coverage=0.8,
        roe=2.0,
        roce=3.5,
        rev_3y_cagr=-5.0,
        pat_3y_cagr=-12.0,
        ocf_to_pat=0.3,
        promoter_holding=30.0,
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
        promoter_holding=55.0,
        pledge_pct=35.0,
    )
    result = score_fundamentals(snap)
    assert result.score == 40.0
    assert any("VETO" in ev and "capped at 40.0" in ev for ev in result.evidence)


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
        roe=20.0,
        roce=24.0,
        rev_3y_cagr=15.0,
        pat_3y_cagr=12.0,
        ocf_to_pat=1.0,
        promoter_holding=55.0,
        pledge_pct=0.0,
    )
    result = score_fundamentals(snap)
    body = "\n".join(result.evidence)
    assert "D/E" in body
    assert "Interest coverage" in body
    assert "ROE" in body
    assert "ROCE" in body
    assert "Revenue 3y CAGR" in body
    assert "PAT 3y CAGR" in body
    assert "OCF/PAT" in body
    assert "Promoter holding" in body
    assert "Promoter pledge" in body
