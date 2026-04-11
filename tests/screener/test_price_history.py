"""Price history + RSI14 + SQLite store tests — no synthesised data."""

from __future__ import annotations

from screener.price_history import PriceHistoryStore, rsi14


def test_rsi14_flat_series_is_neutral():
    closes = [100.0] * 30
    assert rsi14(closes) == 50.0


def test_rsi14_monotonically_rising_approaches_100():
    closes = [100.0 + i for i in range(30)]
    assert rsi14(closes) >= 90


def test_rsi14_monotonically_falling_approaches_0():
    closes = [100.0 - i for i in range(30)]
    assert rsi14(closes) <= 10


def test_rsi14_insufficient_data_defaults_to_50():
    assert rsi14([100.0, 101.0, 99.0]) == 50.0


def test_price_history_store_roundtrip():
    with PriceHistoryStore(":memory:") as store:
        rows = [(f"2026-04-{i+1:02d}", 100.0 + i) for i in range(20)]
        assert store.load_rows("TEST", rows) == 20
        assert store.latest("TEST") == 119.0
        assert store.closes("TEST")[-1] == 119.0
        snap = store.snapshot("TEST")
        assert snap is not None
        assert snap.current_price == 119.0
        assert snap.high_52w == 119.0
        assert snap.low_52w == 100.0


def test_price_history_snapshot_needs_enough_data():
    with PriceHistoryStore(":memory:") as store:
        # Only 10 rows — below the 15-row minimum.
        store.load_rows("TEST", [(f"2026-04-{i+1:02d}", 100.0) for i in range(10)])
        assert store.snapshot("TEST") is None


def test_price_history_snapshot_real_decline_profile():
    """Series that actually declines → SMA50 above current, RSI14 well below 40."""
    closes: list[tuple[str, float]] = []
    for i in range(60):
        # Rising leg: days 0..29 go 60 → 120
        # Falling leg: days 30..59 go 120 → 75
        if i < 30:
            close = 60.0 + (120.0 - 60.0) * (i / 29)
        else:
            close = 120.0 + (75.0 - 120.0) * ((i - 30) / 29)
        closes.append((f"2026-{((i // 30) + 1):02d}-{((i % 30) + 1):02d}", close))

    with PriceHistoryStore(":memory:") as store:
        store.load_rows("DIP", closes)
        snap = store.snapshot("DIP")

    assert snap is not None
    assert snap.high_52w == 120.0
    assert snap.current_price == 75.0
    assert snap.sma_50 > snap.current_price
    assert snap.rsi14 < 40


def test_price_history_delete_removes_rows():
    with PriceHistoryStore(":memory:") as store:
        store.load_rows("X", [("2026-04-01", 100.0), ("2026-04-02", 101.0)])
        assert store.latest("X") == 101.0
        assert store.delete("X") == 2
        assert store.latest("X") is None
