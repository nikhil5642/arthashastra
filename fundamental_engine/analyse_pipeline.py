"""Full analysis pipeline — fetch real data, score deterministically, merge
any Claude-written judgments from the judgment cache.

No LLM calls. The qualitative pillars (leadership / prospects / news) come
from JudgmentStore entries that Claude writes via the `/analyse-stock` skill.
When a judgment is not yet available, the pillar falls back to a clearly
labelled `pending_judgment` placeholder so the caller knows a human/Claude
pass is needed before acting on the composite.

`analyse_ticker` does the fetch + score in one shot and is what the
`analyse` CLI calls. `score_snapshot` is pure scoring (no I/O) and lets
callers like the engine run the fetch themselves when they also need the
raw price history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_sources.fundamentals_source import (
    BalanceSheetSnapshot,
    FetchResult,
    FundamentalsSource,
)
from fundamental_engine.judgments import Judgment, JudgmentStore, PillarJudgment
from scorers.base import ScorerResult, StockScore
from scorers.composite_scorer import blend, pending_judgment
from scorers.fundamentals_scorer import score_fundamentals

log = logging.getLogger(__name__)


class NoDataError(RuntimeError):
    pass


@dataclass(slots=True)
class AnalysisResult:
    snapshot: BalanceSheetSnapshot
    score: StockScore
    judgment: Judgment | None
    price_history: list[tuple[str, float]]


async def analyse_ticker(
    ticker: str,
    *,
    data_cache_dir: Path,
) -> AnalysisResult:
    """Fetch + score convenience wrapper used by the CLI and the engine."""
    fetch = await FundamentalsSource().fetch_full(ticker)
    return score_snapshot(
        fetch.snapshot,
        data_cache_dir=data_cache_dir,
        price_history=fetch.price_history,
    )


def score_snapshot(
    snap: BalanceSheetSnapshot,
    *,
    data_cache_dir: Path,
    price_history: list[tuple[str, float]] | None = None,
) -> AnalysisResult:
    """Pure scoring — no network. Use when the caller already has a snapshot."""
    if snap.data_completeness_pct == 0.0:
        raise NoDataError(f"No fundamentals data for {snap.ticker}")

    fundamentals_result = score_fundamentals(snap)

    judgment = JudgmentStore(data_cache_dir).get(snap.ticker)
    leadership_result = _result_from_pillar(
        judgment.leadership if judgment else None, "leadership"
    )
    prospects_result = _result_from_pillar(
        judgment.prospects if judgment else None, "prospects"
    )
    news_result = _result_from_pillar(
        judgment.news if judgment else None, "news"
    )

    score = blend(
        ticker=snap.ticker,
        leadership=leadership_result,
        fundamentals=fundamentals_result,
        prospects=prospects_result,
        news=news_result,
    )
    return AnalysisResult(
        snapshot=snap,
        score=score,
        judgment=judgment,
        price_history=list(price_history or []),
    )


def build_extended_score_dict(
    result: AnalysisResult | FetchResult | Any,
) -> dict[str, Any]:
    """Single source of truth for the JSON shape persisted to scores.json.

    Accepts either an AnalysisResult (preferred) or any object that exposes
    `.score` (StockScore) and `.snapshot` (BalanceSheetSnapshot) attributes.
    """
    extended = result.score.to_dict()
    snap = result.snapshot
    extended.update(
        {
            "company_name": snap.company_name,
            "sector": snap.sector,
            "industry": snap.industry,
            "current_price": snap.current_price,
            "high_52w": snap.high_52w,
            "low_52w": snap.low_52w,
            "market_cap_cr": snap.market_cap_cr,
            "source": snap.source,
        }
    )
    return extended


def _result_from_pillar(
    pillar: PillarJudgment | None, name: str
) -> ScorerResult:
    if pillar is None:
        return pending_judgment(name)
    return ScorerResult(
        pillar=name,
        score=pillar.score,
        confidence=0.85,
        evidence=list(pillar.evidence),
        data_completeness_pct=85.0,
    )
