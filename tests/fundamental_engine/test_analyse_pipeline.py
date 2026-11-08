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


def _fresh_snap(ticker: str = "MSFT") -> BalanceSheetSnapshot:
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
        insider_holding=50.0,
        pledge_pct=0.0,
        current_price=530.61,
        high_52w=559.15,
        low_52w=348.54,
        market_cap=3.94e12,
        exchange="NasdaqGS",
        currency="USD",
        financial_currency="USD",
        country="United States",
        company_name="Microsoft Corporation",
        sector="Technology",
        industry="Software - Infrastructure",
        source="yfinance",
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

    result = await analyse_ticker("MSFT", data_cache_dir=tmp_path)
    assert isinstance(result, AnalysisResult)
    assert result.snapshot.ticker == "MSFT"
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
            ticker="MSFT",
            leadership=PillarJudgment(
                score=82,
                evidence=["Proven capital allocation"],
                extras={"mode": "PROFESSIONAL"},
            ),
            prospects=PillarJudgment(
                score=78, evidence=["Strong moat in cloud and productivity software"]
            ),
            news=PillarJudgment(
                score=68, evidence=["Positive Q4 results"]
            ),
            leader_name="Satya Nadella",
        )
    )

    result = await analyse_ticker("MSFT", data_cache_dir=tmp_path)
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
    assert extended.score.ticker == "MSFT"
    assert extended.company_name == "Microsoft Corporation"
    assert extended.sector == "Technology"
    assert extended.country == "United States"
    assert extended.currency == "USD"
    assert extended.current_price == 530.61
    assert extended.market_cap == 3.94e12
    assert extended.source == "yfinance"

    # Flat-dict serialization (the scores.json row shape).
    as_dict = extended.to_dict()
    assert as_dict["ticker"] == "MSFT"
    assert as_dict["company_name"] == "Microsoft Corporation"
    assert "composite" in as_dict
    assert "verdict" in as_dict

    # Round-trip through from_dict — must preserve the same values.
    reloaded = ExtendedScore.from_dict(as_dict)
    assert reloaded is not None
    assert reloaded.score.composite == extended.score.composite
    assert reloaded.score.verdict == extended.score.verdict
    assert reloaded.company_name == extended.company_name


def test_integrity_veto_in_judgment_caps_the_composite(tmp_path: Path):
    JudgmentStore(tmp_path).save(
        Judgment(
            ticker="MSFT",
            leadership=PillarJudgment(score=30, evidence=["SEC charges over revenue recognition"]),
            prospects=PillarJudgment(score=80),
            news=PillarJudgment(score=60),
            integrity_veto=True,
        )
    )
    result = score_snapshot(_fresh_snap(), data_cache_dir=tmp_path)
    assert result.score.composite <= 54.0
    assert result.score.verdict == "AVOID"

    # The gate survives the scores.json round trip, so `pick` can block it.
    reloaded = ExtendedScore.from_dict(ExtendedScore.from_analysis(result).to_dict())
    assert reloaded is not None
    assert reloaded.score.gates_applied == result.score.gates_applied
