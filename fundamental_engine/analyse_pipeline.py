"""Full analysis pipeline — fetch real data, score deterministically, merge
any Claude-written judgments from the judgment cache.

No LLM calls. The qualitative pillars (leadership / prospects / news) come
from JudgmentStore entries that Claude writes via the `/analyse-stock` skill.
When a judgment is not yet available, the pillar falls back to a clearly
labelled `pending_judgment` placeholder so the caller knows a human/Claude
pass is needed before acting on the composite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from data_sources.fundamentals_source import (
    BalanceSheetSnapshot,
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


async def analyse_ticker(
    ticker: str,
    *,
    data_cache_dir: Path,
) -> AnalysisResult:
    snap = await FundamentalsSource().fetch(ticker)
    if snap.data_completeness_pct == 0.0:
        raise NoDataError(f"No fundamentals data for {ticker}")

    fundamentals_result = score_fundamentals(snap)

    judgment = JudgmentStore(data_cache_dir).get(ticker)
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
    return AnalysisResult(snapshot=snap, score=score, judgment=judgment)


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
