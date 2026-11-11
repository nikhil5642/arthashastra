"""Signal bus tests — round trip, filtering, bounds, tolerant parsing."""

from __future__ import annotations

import json
from pathlib import Path

from signals.signal_bus import Severity, Signal, SignalBus, SignalType


def test_severity_ranks_from_info_to_urgent():
    assert Severity.INFO.rank < Severity.ALERT.rank < Severity.URGENT.rank
    assert max(Severity, key=lambda s: s.rank) is Severity.URGENT


def test_emit_and_read_roundtrip(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    sig = Signal(
        signal_type=SignalType.DIP_DETECTED,
        severity=Severity.ALERT,
        ticker="MSFT",
        message="Drawdown 22% from 52w high; composite 82",
        data={"composite": 82, "drawdown_pct": 22.0},
    )
    bus.emit(sig)

    read_back = bus.read_all()
    assert len(read_back) == 1
    only = read_back[0]
    assert only.signal_type == SignalType.DIP_DETECTED
    assert only.severity == Severity.ALERT
    assert only.ticker == "MSFT"
    assert only.data["composite"] == 82
    assert only.timestamp, "timestamp should be auto-populated"


def test_read_urgent_filters(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.INFO, "A", "info"))
    bus.emit(Signal(SignalType.EARLY_CONCERN, Severity.URGENT, "B", "urgent"))
    bus.emit(Signal(SignalType.VERDICT_CHANGED, Severity.ALERT, "C", "alert"))

    urgent = bus.read_urgent()
    assert len(urgent) == 1
    assert urgent[0].ticker == "B"
    assert urgent[0].signal_type == SignalType.EARLY_CONCERN


def test_read_for_ticker_is_case_insensitive(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.ALERT, "MSFT", "x"))
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.ALERT, "0700.HK", "y"))

    assert len(bus.read_for_ticker("msft")) == 1
    assert len(bus.read_for_ticker("MSFT")) == 1
    assert len(bus.read_for_ticker("JPM")) == 0


def test_max_signals_kept_is_enforced(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json", max_kept=5)
    for i in range(10):
        bus.emit(Signal(SignalType.SCORE_UPDATED, Severity.INFO, f"T{i}", f"msg{i}"))

    kept = bus.read_all()
    assert len(kept) == 5
    assert [s.ticker for s in kept] == [f"T{i}" for i in range(5, 10)]


def test_all_signal_types_roundtrip_through_file(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json", max_kept=len(SignalType) + 10)
    for st in SignalType:
        bus.emit(Signal(st, Severity.INFO, "TEST", st.value))

    read_back = bus.read_all()
    assert {s.signal_type for s in read_back} == set(SignalType)


def test_empty_or_missing_file_returns_empty_list(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "missing.json")
    assert bus.read_all() == []
    assert bus.read_urgent() == []

    bus.clear()
    assert bus.read_all() == []


def test_read_unread_filters_by_timestamp(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.INFO, "X", "old", timestamp="2026-01-01T00:00:00"))
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.INFO, "Y", "new", timestamp="2026-04-11T10:00:00"))

    unread = bus.read_unread(last_read_ts="2026-03-01T00:00:00")
    assert len(unread) == 1
    assert unread[0].ticker == "Y"


def test_corrupt_rows_are_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "signals.json"
    path.write_text(
        '[{"signal_type": "NOT_A_TYPE", "severity": "INFO", "message": "x"},'
        ' "not even an object",'
        ' {"signal_type": "SCORE_UPDATED", "severity": "INFO", "ticker": "OK", "message": "y"}]'
    )
    bus = SignalBus(path=path)
    assert [s.ticker for s in bus.read_all()] == ["OK"]


def test_non_list_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "signals.json"
    path.write_text('{"oops": true}')
    assert SignalBus(path=path).read_all() == []


def test_severity_formats_as_its_value() -> None:
    assert f"{Severity.URGENT}" == "URGENT"


def test_emit_many_appends_in_one_write_and_keeps_the_cap(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json", max_kept=3)
    bus.emit_many([])  # nothing to write: no file is created
    assert not (tmp_path / "signals.json").exists()

    bus.emit_many([
        Signal(SignalType.SCORE_UPDATED, Severity.INFO, "GOOG", "composite 66.6 (HOLD)"),
        Signal(SignalType.VERDICT_CHANGED, Severity.ALERT, "GOOG", "BUY → HOLD"),
        Signal(SignalType.EARLY_CONCERN, Severity.URGENT, "GOOG", "44% of profit is one-offs"),
        Signal(SignalType.EARLY_STRENGTH, Severity.ALERT, "GOOG", "Revenue growth picked up"),
    ])
    assert [s.signal_type for s in bus.read_all()] == [
        SignalType.VERDICT_CHANGED, SignalType.EARLY_CONCERN, SignalType.EARLY_STRENGTH,
    ]


def test_rows_of_a_retired_signal_type_are_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "signals.json"
    path.write_text(json.dumps([
        {"signal_type": "LEADERSHIP_RED_FLAG", "severity": "URGENT", "ticker": "X", "message": "old"},
        {"signal_type": "DIP_DETECTED", "severity": "ALERT", "ticker": "Y", "message": "kept"},
    ]))
    assert [s.ticker for s in SignalBus(path=path).read_all()] == ["Y"]
