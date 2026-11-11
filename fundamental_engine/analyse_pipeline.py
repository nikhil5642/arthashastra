"""Full analysis pipeline — fetch real data, score deterministically, merge
any Claude-written judgments from the judgment cache.

No LLM calls. The qualitative pillars (leadership / prospects / news) come
from JudgmentStore entries that Claude writes via the `/analyse-stock` skill.
When a judgment is not yet available, the pillar falls back to a clearly
labelled `pending_judgment` placeholder so the caller knows a human/Claude
pass is needed before acting on the composite.

Judgments age. A cached judgment was right on the day it was written:
  - the news pillar judges a rolling 30-day window, so once the judgment is
    older than that window it describes news that no longer counts — the
    pillar goes back to pending;
  - leadership and prospects move slowly but not never — after 90 days they
    still count, at lower confidence, with a STALE line in the evidence;
  - an integrity veto never expires: a fraud finding is not undone by time.

`analyse_ticker` does the fetch + score in one shot and is what the
`analyse` CLI calls. `score_snapshot` is pure scoring (no I/O) and lets
callers like the engine run the fetch themselves when they also need the
raw price history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from data_sources.fundamentals_source import (
    BalanceSheetSnapshot,
    FundamentalsSource,
)
from data_sources.news_source import RADAR_WINDOW_DAYS, NewsDigest, NewsSource, digest
from fundamental_engine.judgments import Judgment, JudgmentStore, PillarJudgment
from scorers.base import Pillar, ScorerResult, StockScore
from scorers.composite_scorer import NEUTRAL_PRIOR_SCORE, blend, pending_judgment
from scorers.early_signals import EarlySignal, detect_early_signals
from scorers.fundamentals_scorer import score_fundamentals
from scorers.valuation_scorer import score_valuation

log = logging.getLogger(__name__)

NEWS_WINDOW_DAYS = 30  # what the news pillar judges; older judgments describe expired news
STALE_JUDGMENT_DAYS = 90  # leadership / prospects: still counted, but flagged
JUDGMENT_CONFIDENCE = 0.85
STALE_JUDGMENT_CONFIDENCE = 0.5
JUDGMENT_COMPLETENESS_PCT = 85.0


class NoDataError(RuntimeError):
    pass


@dataclass(slots=True)
class AnalysisResult:
    snapshot: BalanceSheetSnapshot
    score: StockScore
    judgment: Judgment | None
    price_history: list[tuple[str, float]]
    # Concerns and strengths the snapshot shows beyond the scored ratios.
    early_signals: list[EarlySignal] = field(default_factory=list)


async def analyse_ticker(
    ticker: str,
    *,
    data_cache_dir: Path,
    sec_user_agent: str = "",
) -> AnalysisResult:
    """Fetch + score convenience wrapper used by the CLI."""
    fetch = await FundamentalsSource(sec_user_agent=sec_user_agent).fetch_full(ticker)
    return score_snapshot(
        fetch.snapshot,
        data_cache_dir=data_cache_dir,
        price_history=fetch.price_history,
        news=await news_digest_for(fetch.snapshot),
    )


async def news_digest_for(snap: BalanceSheetSnapshot) -> NewsDigest | None:
    """The last week's story counts for the company — the news half of the early signals.

    None when the company has no name to search on or the feed can't be
    read; a missing digest produces no news signal either way.
    """
    if not snap.company_name:
        return None
    try:
        stories = await NewsSource().fetch_for_company(
            snap.company_name, ticker=snap.ticker, within_days=RADAR_WINDOW_DAYS
        )
    except Exception as e:
        log.warning("news radar failed for %s: %s", snap.ticker, e)
        return None
    return digest(stories)


def score_snapshot(
    snap: BalanceSheetSnapshot,
    *,
    data_cache_dir: Path,
    price_history: list[tuple[str, float]] | None = None,
    news: NewsDigest | None = None,
) -> AnalysisResult:
    """Pure scoring — no network. Use when the caller already has a snapshot."""
    if snap.data_completeness_pct == 0.0:
        raise NoDataError(f"No fundamentals data for {snap.ticker}")

    judgment = JudgmentStore(data_cache_dir).get(snap.ticker)
    age_days = judgment.age_days if judgment else None

    score = blend(
        ticker=snap.ticker,
        fundamentals=score_fundamentals(snap),
        valuation=score_valuation(snap),
        leadership=_judged(judgment.leadership if judgment else None, Pillar.LEADERSHIP, age_days),
        prospects=_judged(judgment.prospects if judgment else None, Pillar.PROSPECTS, age_days),
        news=_judged(judgment.news if judgment else None, Pillar.NEWS, age_days),
        integrity_veto=judgment.integrity_veto if judgment else False,
    )
    return AnalysisResult(
        snapshot=snap,
        score=score,
        judgment=judgment,
        price_history=list(price_history or []),
        early_signals=detect_early_signals(snap, news=news),
    )


def _judged(pillar: PillarJudgment | None, name: Pillar, age_days: int | None) -> ScorerResult:
    """A Claude-judged pillar as a ScorerResult, discounted for its age.

    An age that can't be read counts as old: unknown is not good.
    """
    if pillar is None:
        return pending_judgment(name)

    age = "an unknown time" if age_days is None else f"{age_days} days"
    if name is Pillar.NEWS and (age_days is None or age_days > NEWS_WINDOW_DAYS):
        return ScorerResult(
            pillar=name,
            score=NEUTRAL_PRIOR_SCORE,
            confidence=0.0,
            evidence=[
                f"EXPIRED: the news judgment was written {age} ago — the "
                f"{NEWS_WINDOW_DAYS}-day window it covered has passed. Run /analyse-stock "
                f"for current news; until then this pillar is neutral."
            ],
            data_completeness_pct=0.0,
        )

    stale = age_days is None or age_days > STALE_JUDGMENT_DAYS
    evidence = list(pillar.evidence)
    if stale:
        evidence.insert(
            0,
            f"STALE: judged {age} ago — run /analyse-stock before relying on this pillar.",
        )
    return ScorerResult(
        pillar=name,
        score=pillar.score,
        confidence=STALE_JUDGMENT_CONFIDENCE if stale else JUDGMENT_CONFIDENCE,
        evidence=evidence,
        data_completeness_pct=JUDGMENT_COMPLETENESS_PCT,
    )
