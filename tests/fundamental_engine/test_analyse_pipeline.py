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

    monkeypatch.setattr(analyse_pipeline, "FundamentalsSource", lambda **_: FakeSource())


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


# ── Judgments age ────────────────────────────────────────────────────────────


def _judged(tmp_path: Path, *, days_old: int | None) -> AnalysisResult:
    from datetime import datetime, timedelta

    updated_at = (
        "not a timestamp" if days_old is None
        else (datetime.now() - timedelta(days=days_old)).isoformat(timespec="seconds")
    )
    JudgmentStore(tmp_path).save(
        Judgment(
            ticker="MSFT",
            leadership=PillarJudgment(score=82, evidence=["Proven capital allocation"]),
            prospects=PillarJudgment(score=78),
            news=PillarJudgment(score=20, evidence=["Regulator opens probe"]),
            updated_at=updated_at,
        )
    )
    return score_snapshot(_fresh_snap(), data_cache_dir=tmp_path)


def test_a_fresh_judgment_counts_in_full(tmp_path: Path):
    score = _judged(tmp_path, days_old=3).score
    assert (score.leadership_score, score.prospects_score, score.news_score) == (82.0, 78.0, 20.0)
    assert not any(line.startswith(("STALE", "EXPIRED")) for lines in score.evidence.values() for line in lines)


def test_the_cached_row_remembers_which_pillars_were_never_scored(tmp_path: Path):
    from fundamental_engine.extended_score import ExtendedScore
    from scorers.base import Pillar

    unjudged = score_snapshot(_fresh_snap(), data_cache_dir=tmp_path)
    assert {Pillar.LEADERSHIP, Pillar.PROSPECTS, Pillar.NEWS} <= set(unjudged.score.unscored)
    row = ExtendedScore.from_analysis(unjudged).to_dict()
    reloaded = ExtendedScore.from_dict(row)
    assert reloaded is not None and reloaded.score.unscored == unjudged.score.unscored
    # A row written before the field existed, or one carrying a pillar from the future, still loads.
    legacy = ExtendedScore.from_dict({k: v for k, v in row.items() if k != "unscored"})
    assert legacy is not None and legacy.score.unscored == []
    odd = ExtendedScore.from_dict(row | {"unscored": ["news", "a_pillar_from_the_future", 7]})
    assert odd is not None and odd.score.unscored == [Pillar.NEWS]

    # An expired news judgment is a placeholder again; fresh leadership and prospects are not.
    expired = _judged(tmp_path, days_old=45).score
    assert Pillar.NEWS in expired.unscored and Pillar.LEADERSHIP not in expired.unscored


def test_a_news_judgment_expires_with_its_thirty_day_window(tmp_path: Path):
    score = _judged(tmp_path, days_old=45).score
    # The probe it judged is outside the window: the pillar is neutral again, not still 20.
    assert score.news_score == 50.0
    assert score.evidence["news"][0].startswith("EXPIRED: the news judgment was written 45 days ago")
    # Leadership and prospects move slowly: still counted, not yet stale.
    assert (score.leadership_score, score.prospects_score) == (82.0, 78.0)
    assert not score.evidence["leadership"][0].startswith("STALE")


def test_old_leadership_and_prospects_judgments_are_kept_but_flagged(tmp_path: Path):
    fresh_confidence = _judged(tmp_path, days_old=3).score.confidence
    stale = _judged(tmp_path, days_old=120).score
    assert stale.leadership_score == 82.0
    assert stale.evidence["leadership"][0].startswith("STALE: judged 120 days ago")
    assert stale.evidence["prospects"][0].startswith("STALE")
    assert stale.confidence < fresh_confidence


def test_a_judgment_of_unknown_age_is_treated_as_old(tmp_path: Path):
    score = _judged(tmp_path, days_old=None).score
    assert score.news_score == 50.0
    assert "an unknown time ago" in score.evidence["news"][0]
    assert score.evidence["leadership"][0].startswith("STALE")


def test_an_integrity_veto_never_expires(tmp_path: Path):
    from datetime import datetime, timedelta

    JudgmentStore(tmp_path).save(
        Judgment(
            ticker="MSFT", leadership=PillarJudgment(score=25), integrity_veto=True,
            updated_at=(datetime.now() - timedelta(days=400)).isoformat(timespec="seconds"),
        )
    )
    assert score_snapshot(_fresh_snap(), data_cache_dir=tmp_path).score.composite <= 54.0


# ── Early signals ride along with every analysis ─────────────────────────────


def test_score_snapshot_reads_the_statements_and_the_news_digest(tmp_path: Path):
    from data_sources.news_source import CategoryDigest, NewsCategory, NewsDigest
    from scorers.early_signals import EarlySignalKind

    snap = _fresh_snap()
    snap.shareholders_equity = -10.0
    news = NewsDigest(window_days=7, stories=10, categories={
        NewsCategory.GOVERNANCE: CategoryDigest(stories=6, widest_headline="Board probes CEO", widest_coverage=2)
    })
    result = score_snapshot(snap, data_cache_dir=tmp_path, news=news)
    assert {s.kind for s in result.early_signals} >= {
        EarlySignalKind.NEGATIVE_EQUITY, EarlySignalKind.GOVERNANCE_NEWS
    }
    # …and survive the scores.json round trip.
    reloaded = ExtendedScore.from_dict(ExtendedScore.from_analysis(result).to_dict())
    assert reloaded is not None and reloaded.early_signals == result.early_signals


async def test_analyse_ticker_reads_the_companys_news_radar(monkeypatch, tmp_path: Path):
    from datetime import datetime

    from data_sources import news_source
    from scorers.early_signals import EarlySignalKind

    _install_fake_source(monkeypatch, _fresh_snap())
    searched: list[str] = []

    def fake_feed(url: str, source_name: str) -> list[dict]:
        searched.append(url)
        now = datetime.now().isoformat()
        return [
            {"headline": f"Microsoft faces fraud investigation, part {i}", "body": "", "url": f"u{i}",
             "published_at": now, "source": "Reuters"}
            for i in range(4)
        ]

    monkeypatch.setattr(news_source, "_fetch_feed_sync", fake_feed)
    result = await analyse_ticker("MSFT", data_cache_dir=tmp_path)
    assert "%22Microsoft%22" in searched[0]
    assert EarlySignalKind.GOVERNANCE_NEWS in {s.kind for s in result.early_signals}


async def test_a_broken_news_feed_costs_only_the_news_signals(monkeypatch, tmp_path: Path):
    from data_sources import news_source

    _install_fake_source(monkeypatch, _fresh_snap())

    async def boom(self, company_name: str, *, ticker: str, within_days: int):
        raise ConnectionError("feed down")

    monkeypatch.setattr(news_source.NewsSource, "fetch_for_company", boom)
    result = await analyse_ticker("MSFT", data_cache_dir=tmp_path)
    assert result.score.composite > 0 and result.early_signals == []


def test_no_data_says_the_symbol_may_have_changed(tmp_path):
    """A retired symbol (Tata Motors after its demerger) should point to `resolve`, not just fail."""
    import pytest
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from fundamental_engine.analyse_pipeline import NoDataError, score_snapshot

    with pytest.raises(NoDataError) as raised:
        score_snapshot(BalanceSheetSnapshot(ticker="TATAMOTORS.NS", as_of_date="2026-11-13"),
                       data_cache_dir=tmp_path)
    assert "TATAMOTORS.NS" in str(raised.value) and "resolve" in str(raised.value)
