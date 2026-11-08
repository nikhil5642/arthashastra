"""Snapshot store tests — pure I/O, tmp_path."""

from __future__ import annotations

from pathlib import Path

from data_sources.fundamentals_source import BalanceSheetSnapshot, DataConflict
from fundamental_engine.snapshot_store import SnapshotStore


def test_snapshot_round_trip_keeps_raw_dumps_and_conflicts(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    snap = BalanceSheetSnapshot(
        ticker="TEST",
        as_of_date="2026-11-08",
        roe=18.0,
        raw_profit_loss={"sales": [100.0, None, 120.0]},
        raw_profit_loss_headers=["Mar 2024", "Mar 2025", "Mar 2026"],
        data_conflicts=[
            DataConflict("debt_to_equity", "screener", 0.52, "yfinance", 1.49, 65.1)
        ],
    )
    store.save(snap)

    loaded = store.get("test")
    assert loaded is not None
    assert loaded.roe == 18.0
    assert loaded.raw_profit_loss == {"sales": [100.0, None, 120.0]}
    assert loaded.data_conflicts == snap.data_conflicts


def test_missing_or_corrupt_snapshot_returns_none(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    assert store.get("NOPE") is None
    store.path_for("BAD").write_text("{not json")
    assert store.get("BAD") is None
