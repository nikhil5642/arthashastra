"""Analyse pipeline tests — mock FundamentalsSource and JudgmentStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_sources.fundamentals_source import BalanceSheetSnapshot, FetchResult
from fundamental_engine import analyse_pipeline
from fundamental_engine.analyse_pipeline import (
    AnalysisResult,
    NoDataError,
    analyse_ticker,
    score_snapshot,
)
from fundamental_engine.extended_score import ExtendedScore
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


def _install_fake_source(
    monkeypatch, snap: BalanceSheetSnapshot, price_history: list | None = None
) -> None:
    class FakeSource:
        async def fetch_full(self, ticker: str) -> FetchResult:
            return FetchResult(snapshot=snap, price_history=price_history or [])

        async def fetch(self, ticker: str) -> BalanceSheetSnapshot:
            return snap

    monkeypatch.setattr(analyse_pipeline, "FundamentalsSource", lambda: FakeSource())


async def test_analyse_ticker_without_judgment_uses_pending(
    monkeypatch, tmp_path: Path
):
    snap = _fresh_snap()
    _install_fake_source(monkeypatch, snap, [("2026-04-01", 1500.0)])

    result = await analyse_ticker("RELIANCE", data_cache_dir=tmp_path)
    assert isinstance(result, AnalysisResult)
    assert result.snapshot.ticker == "RELIANCE"
    assert result.score.fundamentals_score > 70  # deterministic strong fundamentals
    # Pending placeholders use neutral-prior 50 with zero completeness.
    assert result.score.leadership_score == 50.0
    assert result.score.prospects_score == 50.0
    assert result.score.news_score == 50.0
    assert result.judgment is None
    assert result.score.low_confidence is True
    assert result.price_history == [("2026-04-01", 1500.0)]


async def test_analyse_ticker_with_judgment_merges(
    monkeypatch, tmp_path: Path
):
    _install_fake_source(monkeypatch, _fresh_snap())

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
    empty = BalanceSheetSnapshot(
        ticker="NOPE", as_of_date="2026-04-11", source="empty"
    )
    _install_fake_source(monkeypatch, empty)

    with pytest.raises(NoDataError):
        await analyse_ticker("NOPE", data_cache_dir=tmp_path)


def test_score_snapshot_pure_no_io(tmp_path: Path):
    snap = _fresh_snap()
    result = score_snapshot(snap, data_cache_dir=tmp_path)
    assert isinstance(result, AnalysisResult)
    assert result.snapshot is snap
    assert result.price_history == []


def test_extended_score_captures_snapshot_metadata(tmp_path: Path):
    snap = _fresh_snap()
    result = score_snapshot(snap, data_cache_dir=tmp_path)
    extended = ExtendedScore.from_analysis(result)

    # Typed-object access (the fields business logic uses).
    assert extended.score.ticker == "RELIANCE"
    assert extended.company_name == "Reliance Industries Limited"
    assert extended.sector == "Energy"
    assert extended.industry == "Oil & Gas"
    assert extended.current_price == 1500.0
    assert extended.high_52w == 1800.0
    assert extended.low_52w == 1200.0
    assert extended.market_cap_cr == 100000.0
    assert extended.source == "yfinance+screener"

    # Flat-dict serialization (the scores.json row shape).
    as_dict = extended.to_dict()
    assert as_dict["ticker"] == "RELIANCE"
    assert as_dict["company_name"] == "Reliance Industries Limited"
    assert "composite" in as_dict
    assert "verdict" in as_dict

    # Round-trip through from_dict — must preserve the same values.
    reloaded = ExtendedScore.from_dict(as_dict)
    assert reloaded is not None
    assert reloaded.score.composite == extended.score.composite
    assert reloaded.score.verdict == extended.score.verdict
    assert reloaded.company_name == extended.company_name
