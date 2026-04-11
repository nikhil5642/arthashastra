"""Composite scorer — blends four pillar ScorerResults into a StockScore.

Weights (per PLAN.md §Scoring rubrics):
    leadership   0.40
    fundamentals 0.30
    prospects    0.15
    news         0.15
"""

from __future__ import annotations

from scorers.base import ScorerResult, StockScore, verdict_for

PILLAR_WEIGHTS: dict[str, float] = {
    "leadership": 0.40,
    "fundamentals": 0.30,
    "prospects": 0.15,
    "news": 0.15,
}

NEUTRAL_PRIOR_SCORE = 50.0
PENDING_COMPLETENESS_PCT = 0.0


def blend(
    *,
    ticker: str,
    leadership: ScorerResult,
    fundamentals: ScorerResult,
    prospects: ScorerResult,
    news: ScorerResult,
) -> StockScore:
    pillars = {
        "leadership": leadership,
        "fundamentals": fundamentals,
        "prospects": prospects,
        "news": news,
    }

    composite = round(
        sum(p.score * PILLAR_WEIGHTS[name] for name, p in pillars.items()),
        1,
    )

    mean_completeness = (
        sum(p.data_completeness_pct for p in pillars.values()) / len(pillars)
    )
    mean_conf = sum(p.confidence for p in pillars.values()) / len(pillars)
    confidence = round(mean_conf * (mean_completeness / 100.0), 2)

    return StockScore(
        ticker=ticker,
        leadership_score=round(leadership.score, 1),
        fundamentals_score=round(fundamentals.score, 1),
        prospects_score=round(prospects.score, 1),
        news_score=round(news.score, 1),
        composite=composite,
        verdict=verdict_for(composite),
        confidence=confidence,
        data_completeness_pct=round(mean_completeness, 1),
        evidence={name: list(p.evidence) for name, p in pillars.items()},
    )


def pending_judgment(pillar: str) -> ScorerResult:
    """Placeholder for a pillar that awaits Claude's judgment.

    Use this when no Judgment entry exists in the judgment cache yet. The
    composite reflects a neutral prior with zero confidence so downstream
    consumers (pick, brief) can clearly see that a human / Claude pass is
    needed before acting on the verdict.
    """
    return ScorerResult(
        pillar=pillar,
        score=NEUTRAL_PRIOR_SCORE,
        confidence=0.0,
        evidence=[
            f"{pillar} pillar pending Claude judgment — run /analyse-stock "
            f"in Claude Code to generate it."
        ],
        data_completeness_pct=PENDING_COMPLETENESS_PCT,
    )
