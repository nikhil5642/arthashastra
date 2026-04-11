"""Composite scorer — blends four pillar ScorerResults into a StockScore.

Weights:
    fundamentals 0.40   balance sheet + profitability + growth + cash quality + promoter
    leadership   0.30   integrity, track record, governance (Claude-judged)
    prospects    0.15   moat + tailwind + capex/guidance clarity
    news         0.15   30-day material-event sentiment

Fundamentals is the single largest pillar — a business whose balance
sheet, growth, and cash conversion all hold up together deserves more
weight in the composite than any single qualitative read.
"""

from __future__ import annotations

from scorers.base import Pillar, ScorerResult, StockScore, verdict_for

PILLAR_WEIGHTS: dict[Pillar, float] = {
    Pillar.FUNDAMENTALS: 0.40,
    Pillar.LEADERSHIP: 0.30,
    Pillar.PROSPECTS: 0.15,
    Pillar.NEWS: 0.15,
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
    pillars: dict[Pillar, ScorerResult] = {
        Pillar.LEADERSHIP: leadership,
        Pillar.FUNDAMENTALS: fundamentals,
        Pillar.PROSPECTS: prospects,
        Pillar.NEWS: news,
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


def pending_judgment(pillar: Pillar | str) -> ScorerResult:
    """Placeholder for a pillar that awaits Claude's judgment.

    Use this when no Judgment entry exists in the judgment cache yet.
    The composite reflects a neutral prior with zero confidence so
    downstream consumers (pick, brief) can clearly see that a human /
    Claude pass is needed before acting on the verdict.

    Accepts either a `Pillar` enum or a raw string label for backwards
    compatibility with callers that still pass strings — coerces to
    `Pillar` at the boundary so the stored value is always typed.
    """
    pillar_enum = pillar if isinstance(pillar, Pillar) else Pillar(pillar)
    return ScorerResult(
        pillar=pillar_enum,
        score=NEUTRAL_PRIOR_SCORE,
        confidence=0.0,
        evidence=[
            f"{pillar_enum.value} pillar pending Claude judgment — "
            f"run /analyse-stock in Claude Code to generate it."
        ],
        data_completeness_pct=PENDING_COMPLETENESS_PCT,
    )
