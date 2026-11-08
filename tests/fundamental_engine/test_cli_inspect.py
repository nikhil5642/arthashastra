"""`inspect` CLI tests — prime the cache in tmp_path, read JSON from stdout."""

from __future__ import annotations

import json
from pathlib import Path

from data_sources.fundamentals_source import BalanceSheetSnapshot
from fundamental_engine.__main__ import main
from fundamental_engine.snapshot_store import SnapshotStore


def _prime(cache_dir: Path) -> None:
    SnapshotStore(cache_dir).save(
        BalanceSheetSnapshot(
            ticker="TEST",
            as_of_date="2026-11-08",
            roe=18.0,
            raw_profit_loss={"sales": [100.0, 120.0]},
        )
    )


def test_inspect_prints_one_snapshot_section(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    _prime(tmp_path / "cache")

    assert main(["inspect", "TEST", "--section", "raw_profit_loss"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ticker": "TEST",
        "snapshot": {"raw_profit_loss": {"sales": [100.0, 120.0]}},
    }


def test_inspect_rejects_unknown_sections(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    _prime(tmp_path / "cache")

    assert main(["inspect", "TEST", "--section", "not_a_field"]) == 2
    assert "Unknown snapshot section" in capsys.readouterr().out


def test_inspect_reports_an_empty_cache(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["inspect", "NOPE"]) == 3
    assert "Nothing cached" in capsys.readouterr().out
