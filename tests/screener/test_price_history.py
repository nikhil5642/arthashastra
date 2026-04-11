"""Price history + RSI14 + synthetic series tests."""

from __future__ import annotations

from datetime import date

import pytest

from screener.price_history import (
    PriceHistory,
    rsi14,
    synthesize_from_anchors,
)


def test_rsi14_flat_series_is_neutral():
    closes = [100.0] * 30
    # All zeros gain/loss → guard returns 50
    assert rsi14(closes) == 50.0


def test_rsi14_monotonically_rising_approaches_100():
    closes = [100.0 + i for i in range(30)]
    assert rsi14(closes) >= 90


def test_rsi14_monotonically_falling_approaches_0():
    closes = [100.0 - i for i in range(30)]
    assert rsi14(closes) <= 10


def test_rsi14_insufficient_data_defaults_to_50():
    assert rsi14([100.0, 101.0, 99.0]) == 50.0


def test_price_history_roundtrip():
    ph = PriceHistory(":memory:")
    rows = [(f"2026-04-{i+1:02d}", 100 + i) for i in range(20)]
    assert ph.load_rows("TEST", rows) == 20
    assert ph.latest("TEST") == 119
    assert ph.closes("TEST")[-1] == 119
    snap = ph.snapshot("TEST")
    assert snap is not None
    assert snap.current_price == 119
    assert snap.high_52w == 119
    assert snap.low_52w == 100


def test_synthesize_hits_exact_anchors():
    rows = synthesize_from_anchors(
        ticker="X",
        current_price=80.0,
        high_52w=100.0,
        low_52w=60.0,
        days=252,
        end_date=date(2026, 4, 11),
    )
    closes = [c for _, c in rows]
    assert len(closes) == 252
    assert max(closes) == pytest.approx(100.0)
    assert min(closes) == pytest.approx(60.0)
    assert closes[-1] == pytest.approx(80.0)


def test_synthesize_produces_dip_profile():
    """Current price below high → decline → SMA50 above current → low RSI."""
    ph = PriceHistory(":memory:")
    rows = synthesize_from_anchors(
        ticker="DIP",
        current_price=75.0,
        high_52w=120.0,
        low_52w=60.0,
    )
    ph.load_rows("DIP", rows)
    snap = ph.snapshot("DIP")
    assert snap is not None
    assert snap.current_price == pytest.approx(75.0)
    assert snap.high_52w == pytest.approx(120.0)
    assert snap.low_52w == pytest.approx(60.0)
    # SMA50 is computed across the declining last 50 days — should be above current.
    assert snap.sma_50 > snap.current_price
    # RSI14 over a declining last 14 days should be well below 40.
    assert snap.rsi14 < 40


def test_synthesize_deterministic():
    rows1 = synthesize_from_anchors(
        ticker="X", current_price=80.0, high_52w=100.0, low_52w=60.0
    )
    rows2 = synthesize_from_anchors(
        ticker="X", current_price=80.0, high_52w=100.0, low_52w=60.0
    )
    assert rows1 == rows2
