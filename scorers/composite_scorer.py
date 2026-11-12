"""Composite scorer — blends five pillar ScorerResults into a StockScore.

Weights:
    fundamentals 0.35   balance sheet + profitability + growth + cash quality + ownership
    valuation    0.15   earnings / FCF yield, P/E vs own history, bank P/B vs ROE
    leadership   0.25   integrity, track record, governance (Claude-judged)
    prospects    0.15   moat + tailwind + capex/guidance clarity (Claude-judged)
    news         0.10   30-day material-event sentiment (Claude-judged)

Fundamentals is the single largest pillar: a business whose balance sheet,
growth, and cash conversion all hold up deserves the most weight. Valuation
joined in 2026-11 after the first 30-year backtest showed forward returns
barely differing across fundamentals bands — quality alone ignores the
price paid. Half the composite (fundamentals + valuation) is deterministic.

Gates (applied after the weighted blend):
    integrity veto → composite capped at 54.0 (top of the AVOID band).
    Clamping only the leadership pillar is not enough: a fraud usually
    comes with excellent *reported* fundamentals, so 90/30/70/35 would
    still blend to 60.75 = HOLD.
"""

from __future__ import annotations

from scorers.base import Gate, Pillar, ScorerResult, StockScore, verdict_for

PILLAR_WEIGHTS: dict[Pillar, float] = {
    Pillar.FUNDAMENTALS: 0.35,
    Pillar.VALUATION: 0.15,
    Pillar.LEADERSHIP: 0.25,
    Pillar.PROSPECTS: 0.15,
    Pillar.NEWS: 0.10,
}

NEUTRAL_PRIOR_SCORE = 50.0
PENDING_COMPLETENESS_PCT = 0.0

INTEGRITY_GATE_CEILING = 54.0


def deterministic_composite(fundamentals: float, valuation: float | None) -> float:
    """The half of the composite Python computes, rescaled to 0–100.

    Fundamentals and valuation at their live relative weights (35 : 15),
    with an unscored valuation at the neutral prior, as in the blend. It is
    the one number two companies can be compared on before Claude has judged
    either — and the one the backtests replay, since judgments have no
    history.
    """
    f, v = PILLAR_WEIGHTS[Pillar.FUNDAMENTALS], PILLAR_WEIGHTS[Pillar.VALUATION]
    priced = valuation if valuation is not None else NEUTRAL_PRIOR_SCORE
    return (fundamentals * f + priced * v) / (f + v)


def blend(
    *,
    ticker: str,
    fundamentals: ScorerResult,
    valuation: ScorerResult,
    leadership: ScorerResult,
    prospects: ScorerResult,
    news: ScorerResult,
    integrity_veto: bool = False,
) -> StockScore:
    pillars: dict[Pillar, ScorerResult] = {
        Pillar.FUNDAMENTALS: fundamentals,
        Pillar.VALUATION: valuation,
        Pillar.LEADERSHIP: leadership,
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

    evidence = {name: list(p.evidence) for name, p in pillars.items()}
    gates: list[Gate] = []
    if integrity_veto:
        gates.append(Gate.INTEGRITY_VETO)
        if composite > INTEGRITY_GATE_CEILING:
            evidence[Pillar.LEADERSHIP].append(
                f"GATE: integrity veto — composite capped at "
                f"{INTEGRITY_GATE_CEILING:.1f} (uncapped {composite:.1f}). Strong "
                f"reported numbers do not offset an integrity failure."
            )
            composite = INTEGRITY_GATE_CEILING
        else:
            evidence[Pillar.LEADERSHIP].append(
                f"GATE: integrity veto — composite {composite:.1f} already at or "
                f"below the {INTEGRITY_GATE_CEILING:.1f} ceiling."
            )

    return StockScore(
        ticker=ticker,
        leadership_score=round(leadership.score, 1),
        fundamentals_score=round(fundamentals.score, 1),
        valuation_score=round(valuation.score, 1),
        prospects_score=round(prospects.score, 1),
        news_score=round(news.score, 1),
        composite=composite,
        verdict=verdict_for(composite),
        confidence=confidence,
        data_completeness_pct=round(mean_completeness, 1),
        evidence=evidence,
        gates_applied=gates,
        unscored=[name for name, p in pillars.items() if p.data_completeness_pct == 0],
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
