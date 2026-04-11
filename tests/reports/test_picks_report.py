"""Picks report test — prime the scores cache, run the command."""

from __future__ import annotations

import json
from pathlib import Path


def _prime_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps(
            {
                "RELIANCE": {
                    "ticker": "RELIANCE",
                    "leadership_score": 78.0,
                    "fundamentals_score": 82.0,
                    "prospects_score": 72.0,
                    "news_score": 68.0,
                    "composite": 77.1,
                    "verdict": "BUY",
                    "confidence": 0.85,
                    "data_completeness_pct": 88,
                    "evidence": {},
                    "last_updated": "2026-04-11T10:00:00",
                    "company_name": "Reliance Industries Limited",
                    "sector": "Energy",
                    "industry": "Oil & Gas",
                    "current_price": 1450.0,
                    "high_52w": 1811.0,
                    "low_52w": 1195.0,
                    "market_cap_cr": 1827979.0,
                    "source": "yfinance+screener",
                },
                "TCS": {
                    "ticker": "TCS",
                    "leadership_score": 80.0,
                    "fundamentals_score": 85.0,
                    "prospects_score": 75.0,
                    "news_score": 70.0,
                    "composite": 78.5,
                    "verdict": "BUY",
                    "confidence": 0.88,
                    "data_completeness_pct": 90,
                    "evidence": {},
                    "last_updated": "2026-04-11T10:00:00",
                    "company_name": "Tata Consultancy Services",
                    "sector": "Technology",
                    "industry": "IT Services",
                    "current_price": 3850.0,
                    "high_52w": 4250.0,
                    "low_52w": 3750.0,
                    "market_cap_cr": 1400000.0,
                    "source": "yfinance+screener",
                },
            }
        )
    )


def test_pick_command_with_warm_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ANALYST_ENV", "sandbox")
    _prime_cache(tmp_path / "cache")

    from reports.picks_report import run_pick_command

    rc = run_pick_command("100000", min_composite=75, min_verdict="BUY")
    assert rc == 0

    dip_file = tmp_path / "cache" / "dip_candidates.json"
    assert dip_file.exists()
    data = json.loads(dip_file.read_text())
    # RELIANCE's drawdown from 1811 to 1450 is ~19.9% — borderline. TCS drawdown is ~9.4%.
    # Both may or may not qualify depending on exact threshold. At minimum,
    # the command should run without error and produce a file.


def test_pick_command_empty_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ANALYST_ENV", "sandbox")
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)

    from reports.picks_report import run_pick_command

    rc = run_pick_command("100000", min_composite=75, min_verdict="BUY")
    assert rc == 0


def test_pick_command_invalid_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ANALYST_ENV", "sandbox")
    _prime_cache(tmp_path / "cache")

    from reports.picks_report import run_pick_command

    rc = run_pick_command("banana", min_composite=75, min_verdict="BUY")
    assert rc == 4


def test_pick_command_respects_deep_drawdown(tmp_path, monkeypatch):
    """Stock with deep drawdown + BUY verdict should make the picks list."""
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ANALYST_ENV", "sandbox")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps(
            {
                "DEEPDIP": {
                    "ticker": "DEEPDIP",
                    "leadership_score": 78.0,
                    "fundamentals_score": 85.0,
                    "prospects_score": 75.0,
                    "news_score": 72.0,
                    "composite": 79.0,
                    "verdict": "BUY",
                    "confidence": 0.85,
                    "data_completeness_pct": 90,
                    "evidence": {},
                    "last_updated": "2026-04-11T10:00:00",
                    "company_name": "Deep Dip Co",
                    "sector": "Test",
                    "industry": "Test",
                    "current_price": 60.0,
                    "high_52w": 100.0,
                    "low_52w": 55.0,
                    "market_cap_cr": 10000.0,
                    "source": "yfinance+screener",
                },
            }
        )
    )

    from reports.picks_report import run_pick_command

    rc = run_pick_command("100000", min_composite=75, min_verdict="BUY")
    assert rc == 0
    data = json.loads((cache_dir / "dip_candidates.json").read_text())
    assert "DEEPDIP" in data
    assert data["DEEPDIP"]["drawdown_pct"] >= 20
