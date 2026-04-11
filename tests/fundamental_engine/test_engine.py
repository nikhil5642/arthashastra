"""Engine (background warmer) tests — mock FundamentalsSource + no LLM."""

from __future__ import annotations

import json
from pathlib import Path

from data_sources.fundamentals_source import BalanceSheetSnapshot
from fundamental_engine import analyse_pipeline
from fundamental_engine.engine import Engine, enqueue_refresh


def _snap(ticker: str, *, complete: bool = True) -> BalanceSheetSnapshot:
    if not complete:
        return BalanceSheetSnapshot(
            ticker=ticker, as_of_date="2026-04-11", source="empty"
        )
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
        company_name=f"{ticker} Limited",
        sector="Test",
        industry="Test Industry",
        source="yfinance+screener",
    )


def _install_source(monkeypatch, ticker_to_snap: dict[str, BalanceSheetSnapshot]):
    class FakeSource:
        async def fetch(self, ticker: str):
            return ticker_to_snap.get(
                ticker,
                BalanceSheetSnapshot(
                    ticker=ticker, as_of_date="2026-04-11", source="empty"
                ),
            )

    monkeypatch.setattr(analyse_pipeline, "FundamentalsSource", lambda: FakeSource())


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYST_ENV", "sandbox")
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))


async def test_refresh_one_success(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(monkeypatch, {"RELIANCE": _snap("RELIANCE")})

    engine = Engine.from_env()
    result = await engine.refresh_one("RELIANCE")

    assert result.status == "ok"
    assert result.composite is not None
    assert result.verdict in {"STRONG_BUY", "BUY", "HOLD", "AVOID", "RED_FLAG"}

    scores = json.loads(
        (tmp_path / "cache" / "scores.json").read_text()
    )
    assert "RELIANCE" in scores
    assert scores["RELIANCE"]["company_name"] == "RELIANCE Limited"
    assert scores["RELIANCE"]["source"] == "yfinance+screener"


async def test_refresh_one_no_data(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(monkeypatch, {})

    engine = Engine.from_env()
    result = await engine.refresh_one("NOPE")
    assert result.status == "no_data"


async def test_refresh_all_walks_emergent_universe(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(
        monkeypatch,
        {"RELIANCE": _snap("RELIANCE"), "TCS": _snap("TCS")},
    )

    # Seed cache so the universe is non-empty.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps({"RELIANCE": {}, "TCS": {}})
    )

    engine = Engine.from_env()
    results = await engine.refresh_all()
    statuses = {r.ticker: r.status for r in results}
    assert statuses == {"RELIANCE": "ok", "TCS": "ok"}


async def test_refresh_emits_signals(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(monkeypatch, {"RELIANCE": _snap("RELIANCE")})

    engine = Engine.from_env()
    await engine.refresh_one("RELIANCE")

    signals = json.loads(
        (tmp_path / "cache" / "signals.json").read_text()
    )
    assert any(
        s["signal_type"] == "SCORE_UPDATED" and s["ticker"] == "RELIANCE"
        for s in signals
    )


async def test_refresh_queue_drained(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(
        monkeypatch,
        {"RELIANCE": _snap("RELIANCE"), "TCS": _snap("TCS")},
    )

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    enqueue_refresh(cache_dir, "RELIANCE")
    enqueue_refresh(cache_dir, "TCS")

    engine = Engine.from_env()
    processed = await engine.process_refresh_queue()
    assert processed == 2
    assert not (cache_dir / "refresh_queue.jsonl").exists()

    scores = json.loads((cache_dir / "scores.json").read_text())
    assert {"RELIANCE", "TCS"} <= set(scores)
