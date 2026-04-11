"""Universe loader tests — derives the scan universe from cached scores."""

from __future__ import annotations

import json
from pathlib import Path

from fundamental_engine.universe import (
    load_universe,
    load_universe_with_metadata,
)


def test_load_universe_empty(tmp_path: Path):
    assert load_universe(tmp_path) == []
    assert load_universe_with_metadata(tmp_path) == []


def test_load_universe_reads_scores(tmp_path: Path):
    scores = tmp_path / "scores.json"
    scores.write_text(
        json.dumps(
            {
                "RELIANCE": {"composite": 77.1, "verdict": "BUY", "sector": "Energy"},
                "TCS": {"composite": 80.0, "verdict": "STRONG_BUY", "sector": "Tech"},
                "ITC": {"composite": 65.0, "verdict": "HOLD", "sector": "FMCG"},
            }
        )
    )

    tickers = load_universe(tmp_path)
    assert tickers == ["ITC", "RELIANCE", "TCS"]

    entries = load_universe_with_metadata(tmp_path)
    assert len(entries) == 3
    by_ticker = {e["ticker"]: e for e in entries}
    assert by_ticker["RELIANCE"]["composite"] == 77.1
    assert by_ticker["TCS"]["verdict"] == "STRONG_BUY"
