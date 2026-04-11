"""Picks report test — prime the cache with real SQLite price history + scores."""

from __future__ import annotations

import json
from pathlib import Path

from screener.price_history import PriceHistoryStore


def _prime_scores(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps(
            {
                "DEEPDIP": {
                    "ticker": "DEEPDIP",
                    "leadership_score": 82.0,
                    "fundamentals_score": 86.0,
                    "prospects_score": 74.0,
                    "news_score": 70.0,
                    "composite": 79.0,
                    "verdict": "BUY",
                    "confidence": 0.85,
                    "data_completeness_pct": 88,
                    "evidence": {},
                    "last_updated": "2026-04-11T10:00:00",
                    "company_name": "Deep Dip Co",
                    "sector": "Test",
                    "industry": "Test",
                    "current_price": 60.0,
                    "high_52w": 120.0,
                    "low_52w": 55.0,
                    "market_cap_cr": 10000.0,
                    "source": "yfinance+screener",
                },
                "MILD": {
                    "ticker": "MILD",
                    "leadership_score": 80.0,
                    "fundamentals_score": 82.0,
                    "prospects_score": 74.0,
                    "news_score": 70.0,
                    "composite": 77.5,
                    "verdict": "BUY",
                    "confidence": 0.85,
                    "data_completeness_pct": 88,
                    "evidence": {},
                    "last_updated": "2026-04-11T10:00:00",
                    "company_name": "Mild Co",
                    "sector": "Alt",
                    "industry": "Alt",
                    "current_price": 180.0,
                    "high_52w": 200.0,
                    "low_52w": 170.0,
                    "market_cap_cr": 20000.0,
                    "source": "yfinance+screener",
                },
            }
        )
    )


def _declining_series(start: float, end: float, days: int = 60) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for i in range(days):
        if i < days // 2:
            # rising leg to start
            close = start * (0.8 + 0.2 * (i / (days // 2 - 1)))
        else:
            # declining leg to end
            frac = (i - days // 2) / (days // 2 - 1)
            close = start + (end - start) * frac
        rows.append((f"2026-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", close))
    return rows


def _flat_series(value: float, days: int = 60) -> list[tuple[str, float]]:
    return [
        (f"2026-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", value)
        for i in range(days)
    ]


def _prime_prices(cache_dir: Path) -> None:
    with PriceHistoryStore(cache_dir / "prices.db") as store:
        store.load_rows("DEEPDIP", _declining_series(120.0, 60.0, days=60))
        store.load_rows("MILD", _flat_series(180.0, days=60))


def test_pick_command_finds_real_dip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    cache_dir = tmp_path / "cache"
    _prime_scores(cache_dir)
    _prime_prices(cache_dir)

    from fundamental_engine.commands.pick import run_pick_command

    rc = run_pick_command("100000", min_composite=75, min_verdict="BUY")
    assert rc == 0

    data = json.loads((cache_dir / "dip_candidates.json").read_text())
    assert "DEEPDIP" in data
    assert data["DEEPDIP"]["drawdown_pct"] >= 20
    # The MILD ticker is only 10% below its high — shouldn't trigger.
    assert "MILD" not in data


def test_pick_command_empty_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)

    from fundamental_engine.commands.pick import run_pick_command

    rc = run_pick_command("100000", min_composite=75, min_verdict="BUY")
    assert rc == 0


def test_pick_command_invalid_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    _prime_scores(tmp_path / "cache")
    _prime_prices(tmp_path / "cache")

    from fundamental_engine.commands.pick import run_pick_command

    rc = run_pick_command("banana", min_composite=75, min_verdict="BUY")
    assert rc == 4


def test_pick_command_missing_price_history_gracefully_skipped(
    tmp_path, monkeypatch
):
    """If refresh hasn't persisted price history, the ticker is skipped."""
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    _prime_scores(tmp_path / "cache")
    # Note: no _prime_prices — the SQLite DB doesn't exist yet.

    from fundamental_engine.commands.pick import run_pick_command

    rc = run_pick_command("100000", min_composite=75, min_verdict="BUY")
    assert rc == 0
    data = json.loads((tmp_path / "cache" / "dip_candidates.json").read_text())
    assert data == {}
