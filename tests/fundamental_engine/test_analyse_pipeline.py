"""Analyse pipeline tests — mock FundamentalsSource and JudgmentStore."""

from __future__ import annotations

from pathlib import Path

from data_sources.fundamentals_source import BalanceSheetSnapshot
from fundamental_engine import analyse_pipeline
from fundamental_engine.analyse_pipeline import (
    AnalysisResult,
    NoDataError,
    analyse_ticker,
)
from fundamental_engine.judgments import Judgment, JudgmentStore, PillarJudgment


def _fresh_snap(ticker: str = "RELIANCE") -> BalanceSheetSnapshot:
    return BalanceSheetSnapshot(
        ticker=ticker,
        as_of_date="2026-04-11",
        debt_to_equity=0.4,
        interest_coverage=10.0,
        roe=18.0,
        roce=22.0,
        rev_3y_cagr=14.0,
        pat_3y_cagr=12.0,
        ocf_to_pat=1.1,
        promoter_holding=50.0,
        pledge_pct=0.0,
        current_price=1500.0,
        high_52w=1800.0,
        low_52w=1200.0,
        market_cap_cr=100000.0,
        company_name="Reliance Industries Limited",
        sector="Energy",
        industry="Oil & Gas",
        source="yfinance+screener",
    )


async def test_analyse_ticker_without_judgment_uses_pending(
    monkeypatch, tmp_path: Path
):
    class FakeSource:
        async def fetch(self, ticker: str):
            return _fresh_snap(ticker)

    monkeypatch.setattr(analyse_pipeline, "FundamentalsSource", lambda: FakeSource())

    result = await analyse_ticker("RELIANCE", data_cache_dir=tmp_path)
    assert isinstance(result, AnalysisResult)
    assert result.snapshot.ticker == "RELIANCE"
    assert result.score.fundamentals_score > 70  # deterministic strong fundamentals
    # Stubs for LLM pillars → 50 each with 0 completeness.
    assert result.score.leadership_score == 50.0
    assert result.score.prospects_score == 50.0
    assert result.score.news_score == 50.0
    assert result.judgment is None
    # With pending judgments the completeness is pulled way down.
    assert result.score.low_confidence is True


async def test_analyse_ticker_with_judgment_merges(
    monkeypatch, tmp_path: Path
):
    class FakeSource:
        async def fetch(self, ticker: str):
            return _fresh_snap(ticker)

    monkeypatch.setattr(analyse_pipeline, "FundamentalsSource", lambda: FakeSource())

    store = JudgmentStore(tmp_path)
    store.save(
        Judgment(
            ticker="RELIANCE",
            leadership=PillarJudgment(
                score=82,
                evidence=["Proven capital allocation"],
                extras={"mode": "MULTI_GEN_FAMILY"},
            ),
            prospects=PillarJudgment(
                score=78, evidence=["Strong moat in telecom and retail"]
            ),
            news=PillarJudgment(
                score=68, evidence=["Positive Q4 results"]
            ),
            leader_name="Mukesh Ambani",
        )
    )

    result = await analyse_ticker("RELIANCE", data_cache_dir=tmp_path)
    assert result.judgment is not None
    assert result.score.leadership_score == 82.0
    assert result.score.prospects_score == 78.0
    assert result.score.news_score == 68.0
    assert "Proven capital allocation" in result.score.evidence["leadership"]
    assert result.score.composite > 70


async def test_analyse_ticker_raises_when_no_data(monkeypatch, tmp_path: Path):
    class EmptySource:
        async def fetch(self, ticker: str):
            return BalanceSheetSnapshot(
                ticker=ticker, as_of_date="2026-04-11", source="empty"
            )

    monkeypatch.setattr(analyse_pipeline, "FundamentalsSource", lambda: EmptySource())

    import pytest

    with pytest.raises(NoDataError):
        await analyse_ticker("NOPE", data_cache_dir=tmp_path)
