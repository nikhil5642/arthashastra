"""`compare` tests — a primed cache, a fake engine, the real table and JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from fundamental_engine.cache import JsonStore
from fundamental_engine.commands.compare import run_compare_command
from fundamental_engine.engine import Engine, RefreshResult, RefreshStatus
from fundamental_engine.scorecard import RankBy


def _row(ticker: str, fundamentals: float, valuation: float, **extra: object) -> dict[str, object]:
    return {
        "ticker": ticker, "leadership_score": 50.0, "fundamentals_score": fundamentals,
        "valuation_score": valuation, "prospects_score": 50.0, "news_score": 50.0,
        "composite": 66.0, "verdict": "HOLD", "confidence": 0.4, "data_completeness_pct": 60.0,
        "evidence": {"fundamentals": [f"ROE for {ticker}"], "valuation": [f"P/E for {ticker}"]},
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "company_name": f"{ticker} Corp", "country": "United States", "industry": "Semiconductors",
        "early_signals": [], **extra,
    }


@pytest.fixture
def cache(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    store = JsonStore(tmp_path / "scores.json")
    store.upsert("NVDA", _row("NVDA", 91.9, 75.3))
    store.upsert("AMD", _row("AMD", 62.0, 41.0, unscored=["leadership", "prospects", "news"]))
    store.upsert("RIO.BA", _row("RIO.BA", 80.0, 50.0, unscored=["valuation"],
                                gates_applied=["integrity_veto"]))

    async def never_fetches(self: Engine, ticker: str, *, follow: bool = True) -> RefreshResult:
        return RefreshResult(ticker=ticker, status=RefreshStatus.NO_DATA, detail="not listed")

    monkeypatch.setattr(Engine, "refresh_one", never_fetches)
    return tmp_path


def test_compare_ranks_the_named_companies_and_marks_what_is_missing(cache, capsys):
    assert run_compare_command(["amd", "NVDA", "RIO.BA", "TYPO"], rank_by=RankBy.FUNDAMENTALS) == 0
    out = capsys.readouterr().out
    assert "ranked by fundamentals" in out
    assert out.index("NVDA") < out.index("RIO.BA") < out.index("AMD Corp")
    assert "unscored" in out  # RIO.BA: no usable price ratio
    assert "integrity veto" in out and "not judged" in out
    assert "3 of 3 taken from the cache" in out
    assert "Not scored: TYPO (no_data) — not listed" in out


def test_compare_json_carries_the_evidence_for_each_company(cache, capsys):
    assert run_compare_command(["NVDA", "AMD"], rank_by=RankBy.VALUATION, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ranked_by"] == "valuation"
    assert [card["ticker"] for card in payload["scored"]] == ["NVDA", "AMD"]
    nvda, amd = payload["scored"]
    assert nvda["why"] == {"fundamentals": ["ROE for NVDA"], "valuation": ["P/E for NVDA"]}
    assert nvda["awaiting_judgment"] == [] and amd["awaiting_judgment"] == ["leadership", "prospects", "news"]
    assert nvda["reused"] is True and payload["not_scored"] == []


def test_compare_needs_a_ticker_and_says_when_nothing_could_be_scored(cache, capsys):
    assert run_compare_command([]) == 2
    assert "Name at least one ticker" in capsys.readouterr().out
    assert run_compare_command(["TYPO"]) == 3
    assert "Not scored: TYPO" in capsys.readouterr().out
