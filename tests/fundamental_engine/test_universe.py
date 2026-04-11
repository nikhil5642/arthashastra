"""Universe loader tests — derives the scan universe from cached scores."""

from __future__ import annotations

import json
from pathlib import Path

from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.universe import load_scores, load_universe
from scorers.base import Verdict


def test_load_universe_empty(tmp_path: Path):
    assert load_universe(tmp_path) == []
    assert load_scores(tmp_path) == {}


def test_load_universe_reads_scores(tmp_path: Path):
    scores_file = tmp_path / "scores.json"
    scores_file.write_text(
        json.dumps(
            {
                "RELIANCE": {
                    "composite": 77.1,
                    "verdict": "BUY",
                    "sector": "Energy",
                    "company_name": "Reliance Industries Limited",
                },
                "TCS": {
                    "composite": 80.0,
                    "verdict": "STRONG_BUY",
                    "sector": "Tech",
                    "company_name": "Tata Consultancy Services",
                },
                "ITC": {
                    "composite": 65.0,
                    "verdict": "HOLD",
                    "sector": "FMCG",
                    "company_name": "ITC Limited",
                },
            }
        )
    )

    tickers = load_universe(tmp_path)
    assert tickers == ["ITC", "RELIANCE", "TCS"]

    scores = load_scores(tmp_path)
    assert len(scores) == 3
    assert isinstance(scores["RELIANCE"], ExtendedScore)
    assert scores["RELIANCE"].score.composite == 77.1
    assert scores["RELIANCE"].score.verdict == Verdict.BUY
    assert scores["RELIANCE"].sector == "Energy"
    assert scores["TCS"].score.verdict == Verdict.STRONG_BUY
    assert scores["ITC"].score.composite == 65.0


def test_load_scores_skips_unparseable_rows(tmp_path: Path):
    """Rows missing `composite` / `verdict` (e.g. partial refresh writes)
    should be skipped silently, not crash the caller."""
    (tmp_path / "scores.json").write_text(
        json.dumps(
            {
                "GOOD": {
                    "composite": 70.0,
                    "verdict": "BUY",
                    "sector": "Tech",
                },
                "PARTIAL": {"company_name": "No score yet"},
                "BOGUS_VERDICT": {
                    "composite": 50.0,
                    "verdict": "NOT_A_REAL_VERDICT",
                },
            }
        )
    )
    scores = load_scores(tmp_path)
    assert set(scores) == {"GOOD"}
