"""ID-migration tests — legacy bare NSE keys become Yahoo symbols, once."""

from __future__ import annotations

import json
from pathlib import Path

from fundamental_engine.commands.migrate import run_migrate_command
from screener.price_history import PriceHistoryStore


def _prime_legacy_cache(cache_dir: Path) -> None:
    (cache_dir / "judgments").mkdir(parents=True)
    (cache_dir / "scores.json").write_text(
        json.dumps({"RELIANCE": {"composite": 70.0, "verdict": "BUY", "ticker": "RELIANCE"}})
    )
    (cache_dir / "judgments" / "RELIANCE.json").write_text(
        json.dumps({"ticker": "RELIANCE", "leadership": {"score": 80}})
    )
    (cache_dir / "signals.json").write_text(
        json.dumps([{"signal_type": "SCORE_UPDATED", "severity": "INFO", "ticker": "RELIANCE", "message": "x"}])
    )
    with PriceHistoryStore(cache_dir / "prices.db") as store:
        store.load_rows("RELIANCE", [("2026-11-01", 1400.0)])


def test_migration_rekeys_everything_and_backs_up(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("DATA_CACHE_DIR", str(cache_dir))
    _prime_legacy_cache(cache_dir)

    assert run_migrate_command() == 0

    scores = json.loads((cache_dir / "scores.json").read_text())
    assert set(scores) == {"RELIANCE.NS"}
    assert scores["RELIANCE.NS"]["ticker"] == "RELIANCE.NS"
    judgment = json.loads((cache_dir / "judgments" / "RELIANCE.NS.json").read_text())
    assert judgment["ticker"] == "RELIANCE.NS"
    assert not (cache_dir / "judgments" / "RELIANCE.json").exists()
    assert json.loads((cache_dir / "signals.json").read_text())[0]["ticker"] == "RELIANCE.NS"
    with PriceHistoryStore(cache_dir / "prices.db") as store:
        assert store.tickers() == ["RELIANCE.NS"]
    assert list(tmp_path.glob("cache.backup-*"))


def test_migration_never_runs_twice(tmp_path: Path, monkeypatch):
    """A second run would wrongly turn a real US ticker into MSFT.NS."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("DATA_CACHE_DIR", str(cache_dir))
    _prime_legacy_cache(cache_dir)
    assert run_migrate_command() == 0

    scores = json.loads((cache_dir / "scores.json").read_text())
    scores["MSFT"] = {"composite": 75.0, "verdict": "BUY"}
    (cache_dir / "scores.json").write_text(json.dumps(scores))

    assert run_migrate_command() == 0
    assert "MSFT" in json.loads((cache_dir / "scores.json").read_text())


def test_dry_run_changes_nothing(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("DATA_CACHE_DIR", str(cache_dir))
    _prime_legacy_cache(cache_dir)

    assert run_migrate_command(dry_run=True) == 0
    assert set(json.loads((cache_dir / "scores.json").read_text())) == {"RELIANCE"}
