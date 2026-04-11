"""Market brief test — prime cache, run command, verify Markdown output."""

from __future__ import annotations

import json
from pathlib import Path


def _prime_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps(
            {
                "RELIANCE": {
                    "composite": 77.1,
                    "verdict": "BUY",
                    "sector": "Energy",
                    "source": "yfinance+screener",
                    "last_updated": "2026-04-11T10:00:00",
                },
                "TCS": {
                    "composite": 78.5,
                    "verdict": "BUY",
                    "sector": "Technology",
                    "source": "yfinance+screener",
                    "last_updated": "2026-04-11T10:00:00",
                },
            }
        )
    )
    (cache_dir / "dip_candidates.json").write_text(
        json.dumps(
            {
                "RELIANCE": {
                    "ticker": "RELIANCE",
                    "composite_score": 77.1,
                    "drawdown_pct": 20.5,
                    "severity": "ALERT",
                    "sector": "Energy",
                    "reason": "20.5% off 52w high; below 50DMA",
                }
            }
        )
    )


def test_brief_runs_with_populated_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    _prime_cache(tmp_path / "cache")

    from fundamental_engine.commands.brief import run_brief_command

    rc = run_brief_command()
    assert rc == 0

    reports_dir = tmp_path / "cache" / "reports"
    briefs = list(reports_dir.glob("brief_*.md"))
    assert len(briefs) == 1
    content = briefs[0].read_text()
    assert "Market brief" in content
    assert "RELIANCE" in content
    assert "TCS" in content
    assert "Active dip candidates" in content


def test_brief_runs_on_empty_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)

    from fundamental_engine.commands.brief import run_brief_command

    rc = run_brief_command()
    assert rc == 0
    briefs = list((tmp_path / "cache" / "reports").glob("brief_*.md"))
    assert len(briefs) == 1
