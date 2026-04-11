"""Signal bus tests — shape parity with trading/feeds/signals.py plus analyst-specific types."""

from __future__ import annotations

from pathlib import Path

from signals.signal_bus import Severity, Signal, SignalBus, SignalType


def test_emit_and_read_roundtrip(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    sig = Signal(
        signal_type=SignalType.DIP_DETECTED,
        severity=Severity.ALERT,
        ticker="RELIANCE",
        message="Drawdown 22% from 52w high; composite 82",
        data={"composite": 82, "drawdown_pct": 22.0},
    )
    bus.emit(sig)

    read_back = bus.read_all()
    assert len(read_back) == 1
    only = read_back[0]
    assert only.signal_type == SignalType.DIP_DETECTED
    assert only.severity == Severity.ALERT
    assert only.ticker == "RELIANCE"
    assert only.data["composite"] == 82
    assert only.timestamp, "timestamp should be auto-populated"


def test_read_urgent_filters(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.INFO, "A", "info"))
    bus.emit(Signal(SignalType.LEADERSHIP_RED_FLAG, Severity.URGENT, "B", "urgent"))
    bus.emit(Signal(SignalType.EARNINGS_MISS, Severity.ALERT, "C", "alert"))

    urgent = bus.read_urgent()
    assert len(urgent) == 1
    assert urgent[0].ticker == "B"
    assert urgent[0].signal_type == SignalType.LEADERSHIP_RED_FLAG


def test_read_for_ticker_is_case_insensitive(tmp_path: Path) -> None:
    bus = SignalBus(path=tmp_path / "signals.json")
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.ALERT, "RELIANCE", "x"))
    bus.emit(Signal(SignalType.DIP_DETECTED, Severity.ALERT, "TCS", "y"))

    assert len(bus.read_for_ticker("reliance")) == 1
    assert len(bus.read_for_ticker("RELIANCE")) == 1
    assert len(bus.read_for_ticker("HDFC")) == 0


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
