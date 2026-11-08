"""Analysis-context banner tests — what the reader is told up front."""

from __future__ import annotations

import io
from datetime import datetime, timedelta

from rich.console import Console

from data_sources.fundamentals_source import BalanceSheetSnapshot, DataConflict
from fundamental_engine.analyse_pipeline import AnalysisResult
from fundamental_engine.analysis_context import (
    analysis_context_panel,
    format_age,
    judgment_age_days,
)
from fundamental_engine.judgments import Judgment, PillarJudgment
from scorers.composite_scorer import blend, pending_judgment
from scorers.fundamentals_scorer import score_fundamentals
from scorers.valuation_scorer import score_valuation


def _render(result: AnalysisResult, rows: int) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=200, force_terminal=False).print(
        analysis_context_panel(result, result.snapshot.ticker, rows)
    )
    return buffer.getvalue()


def _result(snap: BalanceSheetSnapshot, judgment: Judgment | None) -> AnalysisResult:
    score = blend(
        ticker=snap.ticker,
        leadership=pending_judgment("leadership"),
        fundamentals=score_fundamentals(snap),
        valuation=score_valuation(snap),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    return AnalysisResult(snapshot=snap, score=score, judgment=judgment, price_history=[])


def test_banner_for_a_bank_with_conflicts_and_no_judgment():
    snap = BalanceSheetSnapshot(
        ticker="JPM", as_of_date="2026-11-08", roe=16.5, sector="Financial Services",
        industry="Banks - Diversified", source="yfinance", latest_annual_period="2025-12-31",
        data_conflicts=[DataConflict("roe", "screener", 16.5, "yfinance", 12.0, 27.3)],
    )
    text = _render(_result(snap, None), rows=0)
    assert "source: yfinance" in text
    assert "not refreshed this run" in text
    assert "1 source conflict" in text
    assert "Bank rubric" in text
    assert "Cached judgment:  none" in text


def test_banner_flags_a_stale_judgment():
    snap = BalanceSheetSnapshot(ticker="MSFT", as_of_date="2026-11-08", roe=30.0)
    old = (datetime.now() - timedelta(days=9)).isoformat(timespec="seconds")
    judgment = Judgment(ticker="MSFT", leadership=PillarJudgment(score=80), leader_name="Satya Nadella", updated_at=old)
    text = _render(_result(snap, judgment), rows=250)
    assert "250 daily closes" in text
    assert "9 days old — STALE" in text
    assert "Satya Nadella" in text


def test_judgment_age_parsing():
    assert judgment_age_days("") is None
    assert judgment_age_days("yesterday") is None
    today = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    assert judgment_age_days(today + "Z") == 0
    assert judgment_age_days(today + "+05:30") == 0
    assert [format_age(d) for d in (None, 0, 1, 5)] == [
        "age unknown", "0 days old", "1 day old", "5 days old",
    ]
