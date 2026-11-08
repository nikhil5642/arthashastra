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
                    "market_cap": 10000.0,
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
                    "market_cap": 20000.0,
                    "source": "yfinance+screener",
                },
            }
        )
    )


def _declining_series(start: float, end: float, days: int = 250) -> list[tuple[str, float]]:
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


def _flat_series(value: float, days: int = 250) -> list[tuple[str, float]]:
    return [
        (f"2026-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", value)
        for i in range(days)
    ]


def _prime_prices(cache_dir: Path) -> None:
    with PriceHistoryStore(cache_dir / "prices.db") as store:
        store.load_rows("DEEPDIP", _declining_series(120.0, 60.0))
        store.load_rows("MILD", _flat_series(180.0))


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


def test_pick_command_blocks_integrity_vetoed_ticker(tmp_path, monkeypatch):
    """A cached integrity gate keeps a ticker out of picks however deep the dip."""
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    cache_dir = tmp_path / "cache"
    _prime_scores(cache_dir)
    _prime_prices(cache_dir)
    scores = json.loads((cache_dir / "scores.json").read_text())
    scores["DEEPDIP"]["gates_applied"] = ["integrity_veto"]
    (cache_dir / "scores.json").write_text(json.dumps(scores))

    from fundamental_engine.commands.pick import run_pick_command

    assert run_pick_command("100000", min_composite=75, min_verdict="BUY") == 0
    data = json.loads((cache_dir / "dip_candidates.json").read_text())
    assert "DEEPDIP" not in data


def test_allocate_buys_a_single_share_of_a_high_priced_stock():
    """No arbitrary share-count minimum: one share of a pricey stock is a position."""
    from fundamental_engine.commands.pick import Candidate, _allocate
    from screener.dip_detector import DipSignal
    from signals.signal_bus import Severity

    dip = DipSignal(
        ticker="PRICEY", current_price=15000.0, high_52w=20000.0, drawdown_pct=25.0,
        below_50dma=True, rsi14=30.0, composite_score=80.0, divergence_bonus=0,
        reason="test", severity=Severity.ALERT, triggered_at="2026-11-08T00:00:00",
    )
    pick = Candidate(
        ticker="PRICEY", name="Pricey Co", sector="Test", country="India",
        currency="INR", current_price=15000.0, price_in_base=15000.0,
        composite=80.0, verdict="BUY", dip=dip,  # type: ignore[arg-type]
    )
    # ₹1L budget, 25% position cap → ₹25k slice: one ₹15k share fits. The old
    # 10-share minimum would have dropped this pick entirely.
    assert _allocate([pick], budget=100000.0) == {"PRICEY": (1, 15000.0)}


def test_allocate_sizes_a_foreign_listing_in_the_base_currency():
    """A $100 share with USD→INR at 88 costs ₹8,800 of an INR budget."""
    from fundamental_engine.commands.pick import Candidate, _allocate
    from screener.dip_detector import DipSignal
    from signals.signal_bus import Severity

    dip = DipSignal(
        ticker="MSFT", current_price=100.0, high_52w=140.0, drawdown_pct=28.6,
        below_50dma=True, rsi14=30.0, composite_score=80.0, divergence_bonus=0,
        reason="test", severity=Severity.ALERT, triggered_at="2026-11-08T00:00:00",
    )
    pick = Candidate(
        ticker="MSFT", name="Microsoft", sector="Technology", country="United States",
        currency="USD", current_price=100.0, price_in_base=8800.0,
        composite=80.0, verdict="BUY", dip=dip,  # type: ignore[arg-type]
    )
    assert _allocate([pick], budget=100000.0) == {"MSFT": (2, 17600.0)}


def test_pick_sizes_usd_krw_and_jpy_listings_from_one_inr_budget(tmp_path, monkeypatch):
    """One ₹ budget, three currencies: every allocation is converted with cached FX."""
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BASE_CURRENCY", "INR")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    rows = {
        "MSFT": ("USD", 88.0, "United States", "Technology"),
        "005930.KS": ("KRW", 0.064, "South Korea", "Technology"),
        "7203.T": ("JPY", 0.58, "Japan", "Consumer Cyclical"),
    }
    (cache_dir / "scores.json").write_text(json.dumps({
        ticker: {
            "ticker": ticker, "composite": 82.0, "verdict": "STRONG_BUY",
            "fundamentals_score": 85.0, "company_name": ticker, "sector": sector,
            "country": country, "currency": currency, "fx_rate_to_base": fx,
        }
        for ticker, (currency, fx, country, sector) in rows.items()
    }))
    with PriceHistoryStore(cache_dir / "prices.db") as store:
        store.load_rows("MSFT", _declining_series(560.0, 400.0))
        store.load_rows("005930.KS", _declining_series(90000.0, 60000.0))
        store.load_rows("7203.T", _declining_series(3600.0, 2500.0))

    from fundamental_engine.commands.pick import run_pick_command

    assert run_pick_command("10L", min_composite=75, min_verdict="BUY") == 0
    picks = json.loads((cache_dir / "dip_candidates.json").read_text())
    assert set(picks) == {"MSFT", "005930.KS", "7203.T"}
    assert {picks[t]["severity"] for t in picks} == {"ALERT"}
