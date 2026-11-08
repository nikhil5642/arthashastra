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
                "MSFT": {
                    "composite": 77.1,
                    "verdict": "BUY",
                    "sector": "Technology",
                    "company_name": "Microsoft Corporation",
                },
                "005930.KS": {
                    "composite": 80.0,
                    "verdict": "STRONG_BUY",
                    "sector": "Technology",
                    "company_name": "Samsung Electronics Co., Ltd.",
                },
                "RELIANCE.NS": {
                    "composite": 65.0,
                    "verdict": "HOLD",
                    "sector": "Energy",
                    "company_name": "Reliance Industries Limited",
                    "market_cap_cr": 1827979.0,  # legacy row: ₹ crore
                },
            }
        )
    )

    tickers = load_universe(tmp_path)
    assert tickers == ["005930.KS", "MSFT", "RELIANCE.NS"]

    scores = load_scores(tmp_path)
    assert len(scores) == 3
    assert isinstance(scores["MSFT"], ExtendedScore)
    assert scores["MSFT"].score.composite == 77.1
    assert scores["MSFT"].score.verdict == Verdict.BUY
    assert scores["MSFT"].sector == "Technology"
    assert scores["005930.KS"].score.verdict == Verdict.STRONG_BUY
    assert scores["RELIANCE.NS"].market_cap == 1827979.0 * 1e7  # legacy crore read as rupees


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


def test_load_scores_skips_rows_that_are_not_objects(tmp_path: Path):
    (tmp_path / "scores.json").write_text(
        json.dumps({"GOOD": {"composite": 70.0, "verdict": "BUY"}, "BROKEN": [1, 2]})
    )
    assert set(load_scores(tmp_path)) == {"GOOD"}
