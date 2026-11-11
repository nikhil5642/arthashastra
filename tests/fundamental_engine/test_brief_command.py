"""Market brief test — prime cache, run command, verify Markdown output."""

from __future__ import annotations

import json
from pathlib import Path


def _prime_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps(
            {
                "NVDA": {
                    "composite": 77.1,
                    "verdict": "BUY",
                    "sector": "Technology",
                    "source": "yfinance",
                    "last_updated": "2026-04-11T10:00:00",
                },
                "ASML.AS": {
                    "composite": 78.5,
                    "verdict": "BUY",
                    "sector": "Technology",
                    "source": "yfinance",
                    "last_updated": "2026-04-11T10:00:00",
                },
            }
        )
    )
    (cache_dir / "dip_candidates.json").write_text(
        json.dumps(
            {
                "NVDA": {
                    "ticker": "NVDA",
                    "composite_score": 77.1,
                    "drawdown_pct": 20.5,
                    "severity": "ALERT",
                    "sector": "Technology",
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
    assert "NVDA" in content
    assert "ASML.AS" in content
    assert "Active dip candidates" in content


def test_brief_runs_on_empty_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)

    from fundamental_engine.commands.brief import run_brief_command

    rc = run_brief_command()
    assert rc == 0
    briefs = list((tmp_path / "cache" / "reports").glob("brief_*.md"))
    assert len(briefs) == 1


def test_brief_lists_integrity_vetoes_and_stale_rows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps(
            {
                "FRAUDCO": {
                    "composite": 54.0,
                    "verdict": "AVOID",
                    "gates_applied": ["integrity_veto"],
                    "last_updated": "2020-01-01T00:00:00",
                }
            }
        )
    )

    from fundamental_engine.commands.brief import run_brief_command

    assert run_brief_command() == 0
    content = next((cache_dir / "reports").glob("brief_*.md")).read_text()
    assert "FRAUDCO: integrity veto" in content
    assert "## Stale rows" in content


def test_brief_surfaces_early_signals_and_recent_changes(tmp_path: Path, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def signal(kind: str, tone: str, severity: str, message: str) -> dict:
        return {"kind": kind, "tone": tone, "severity": severity, "message": message}

    (cache_dir / "scores.json").write_text(json.dumps({
        "GOOG": {
            "composite": 66.6, "verdict": "HOLD", "last_updated": datetime.now().isoformat(),
            "early_signals": [
                signal("one_off_profit", "concern", "URGENT", "44% of profit is one-offs."),
                signal("debt_rising", "concern", "ALERT", "Total debt rose 321%."),
                signal("goodwill_jump", "concern", "INFO", "Goodwill doubled."),
                signal("revenue_accelerating", "strength", "ALERT", "Revenue growth picked up."),
                signal("a_kind_from_the_future", "concern", "ALERT", "skipped, not fatal"),
            ],
        },
        "MSFT": {"composite": 64.8, "verdict": "HOLD", "last_updated": datetime.now().isoformat()},
    }))
    now = datetime.now()
    (cache_dir / "signals.json").write_text(json.dumps([
        {"signal_type": "VERDICT_CHANGED", "severity": "ALERT", "ticker": "GOOG",
         "message": "BUY → HOLD (composite 71.8 → 66.6)", "timestamp": (now - timedelta(days=1)).isoformat()},
        {"signal_type": "EARLY_CONCERN", "severity": "URGENT", "ticker": "NVDA",
         "message": "an old concern", "timestamp": (now - timedelta(days=40)).isoformat()},
        {"signal_type": "SCORE_UPDATED", "severity": "INFO", "ticker": "GOOG",
         "message": "composite 66.6 (HOLD)", "timestamp": now.isoformat()},
    ]))

    from fundamental_engine.commands.brief import run_brief_command

    assert run_brief_command() == 0
    content = next((cache_dir / "reports").glob("brief_*.md")).read_text()
    # The most severe concern leads, with the count of the rest; INFO stays in the stock's own report.
    assert "- GOOG: URGENT · 44% of profit is one-offs.  (+1 more)" in content
    assert "Goodwill doubled" not in content and "skipped, not fatal" not in content
    assert "## Early strengths" in content and "- GOOG: Revenue growth picked up." in content
    assert "— 2 concerns (1 urgent) · 1 strength" not in content  # the tally counts INFO too
    assert "— 3 concerns (1 urgent) · 1 strength" in content
    # Only the last two weeks' verdict moves and new signals count as "what changed".
    assert "BUY → HOLD (composite 71.8 → 66.6)" in content
    assert "an old concern" not in content and "composite 66.6 (HOLD)" not in content
