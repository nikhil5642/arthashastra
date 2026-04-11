"""Composite scorer tests — weighted blend, verdict mapping, pending pillars."""

from __future__ import annotations

from scorers.base import ScorerResult, verdict_for
from scorers.composite_scorer import PILLAR_WEIGHTS, blend, pending_judgment


def _mk(
    pillar: str, score: float, completeness: float = 100.0
) -> ScorerResult:
    return ScorerResult(
        pillar=pillar,
        score=score,
        confidence=round(completeness / 100, 2),
        evidence=[f"{pillar} evidence"],
        data_completeness_pct=completeness,
    )


def test_verdict_thresholds():
    assert verdict_for(95) == "STRONG_BUY"
    assert verdict_for(80) == "STRONG_BUY"
    assert verdict_for(79.9) == "BUY"
    assert verdict_for(70) == "BUY"
    assert verdict_for(69.9) == "HOLD"
    assert verdict_for(55) == "HOLD"
    assert verdict_for(54.9) == "AVOID"
    assert verdict_for(40) == "AVOID"
    assert verdict_for(39.9) == "RED_FLAG"


def test_weights_sum_to_one():
    assert abs(sum(PILLAR_WEIGHTS.values()) - 1.0) < 1e-9


def test_blend_all_perfect_scores():
    score = blend(
        ticker="X",
        leadership=_mk("leadership", 100),
        fundamentals=_mk("fundamentals", 100),
        prospects=_mk("prospects", 100),
        news=_mk("news", 100),
    )
    assert score.composite == 100.0
    assert score.verdict == "STRONG_BUY"
    assert score.confidence == 1.0


def test_blend_respects_weights():
    # Fundamentals is the heaviest pillar at 40%.
    score = blend(
        ticker="X",
        leadership=_mk("leadership", 0),
        fundamentals=_mk("fundamentals", 100),
        prospects=_mk("prospects", 0),
        news=_mk("news", 0),
    )
    assert score.composite == 40.0  # 0.40 × 100
    assert score.verdict == "AVOID"

    # Leadership is the second pillar at 30%.
    score = blend(
        ticker="X",
        leadership=_mk("leadership", 100),
        fundamentals=_mk("fundamentals", 0),
        prospects=_mk("prospects", 0),
        news=_mk("news", 0),
    )
    assert score.composite == 30.0  # 0.30 × 100
    assert score.verdict == "RED_FLAG"


def test_pending_judgment_placeholder():
    result = pending_judgment("leadership")
    assert result.pillar == "leadership"
    assert result.score == 50.0
    assert result.confidence == 0.0
    assert result.data_completeness_pct == 0.0
    assert any("pending" in ev.lower() for ev in result.evidence)


def test_blend_with_pending_pillars_marks_low_confidence():
    score = blend(
        ticker="X",
        leadership=pending_judgment("leadership"),
        fundamentals=_mk("fundamentals", 85),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    assert score.low_confidence is True
    assert score.data_completeness_pct < 60
    assert any("pending" in ev.lower() for ev in score.evidence["leadership"])


def test_evidence_dict_has_all_pillars():
    score = blend(
        ticker="X",
        leadership=_mk("leadership", 60),
        fundamentals=_mk("fundamentals", 60),
        prospects=_mk("prospects", 60),
        news=_mk("news", 60),
    )
    assert set(score.evidence) == {"leadership", "fundamentals", "prospects", "news"}
