"""Dip detector gate tests."""

from __future__ import annotations

import pytest

from scorers.base import StockScore
from screener.dip_detector import (
    DEFAULT_MIN_COMPOSITE,
    MIN_CLOSES_FOR_DIP,
    allowed_verdicts_from_min,
    check_dip,
    dip_rank_key,
)
from screener.price_history import PriceSnapshot


def _score(
    *,
    ticker: str = "TEST",
    composite: float = 80,
    verdict: str = "BUY",
    fundamentals: float = 80,
) -> StockScore:
    return StockScore(
        ticker=ticker,
        leadership_score=80,
        fundamentals_score=fundamentals,
        prospects_score=80,
        news_score=80,
        composite=composite,
        verdict=verdict,  # type: ignore[arg-type]
        confidence=0.9,
        data_completeness_pct=100,
        evidence={"leadership": [], "fundamentals": [], "prospects": [], "news": []},
    )


def _prices(
    *,
    current: float = 70.0,
    high: float = 100.0,
    low: float = 65.0,
    sma_50: float | None = 85.0,
    rsi: float = 25.0,
    closes: int = 250,
) -> PriceSnapshot:
    return PriceSnapshot(
        ticker="TEST",
        current_price=current,
        high_52w=high,
        low_52w=low,
        sma_50=sma_50,
        sma_200=None,
        rsi14=rsi,
        closes_count=closes,
    )


def test_allowed_verdicts_from_min():
    assert allowed_verdicts_from_min("STRONG_BUY") == frozenset({"STRONG_BUY"})
    assert allowed_verdicts_from_min("BUY") == frozenset({"STRONG_BUY", "BUY"})
    assert allowed_verdicts_from_min("HOLD") == frozenset(
        {"STRONG_BUY", "BUY", "HOLD"}
    )


def test_allowed_verdicts_rejects_unknown():
    with pytest.raises(ValueError):
        allowed_verdicts_from_min("STRONG")


def test_fires_on_quality_dip():
    check = check_dip(score=_score(), prices=_prices())
    assert check.qualifies
    assert check.dip is not None
    assert check.dip.severity == "ALERT"
    assert check.dip.drawdown_pct == 30.0
    assert "below 50DMA" in check.dip.reason
    assert "RSI14" in check.dip.reason


def test_rejects_low_composite():
    check = check_dip(score=_score(composite=60), prices=_prices())
    assert not check.qualifies
    assert any("composite" in r for r in check.reasons)


def test_rejects_hold_verdict_by_default():
    check = check_dip(score=_score(verdict="HOLD"), prices=_prices())
    assert not check.qualifies
    assert any("verdict" in r for r in check.reasons)


def test_allows_hold_when_whitelisted():
    check = check_dip(
        score=_score(verdict="HOLD"),
        prices=_prices(),
        allowed_verdicts=allowed_verdicts_from_min("HOLD"),
    )
    assert check.qualifies


def test_rejects_shallow_drawdown():
    check = check_dip(score=_score(), prices=_prices(current=90, high=100))
    assert not check.qualifies
    assert any("drawdown" in r for r in check.reasons)


def test_rejects_insufficient_technical_confirmations():
    # Deep drawdown, but price equals SMA (no below-50DMA), RSI above 40, not near low.
    check = check_dip(
        score=_score(),
        prices=_prices(current=78, high=100, low=50, sma_50=78, rsi=55),
    )
    assert not check.qualifies
    assert any("technical" in r for r in check.reasons)


def test_blocked_ticker_short_circuits():
    check = check_dip(
        score=_score(), prices=_prices(), blocked_tickers={"TEST"}
    )
    assert not check.qualifies
    assert any("RED_FLAG" in r for r in check.reasons)


def test_earnings_divergence_upgrades_severity_to_urgent():
    # 35% drawdown while the latest quarter's profit grew 25% YoY → URGENT.
    check = check_dip(
        score=_score(composite=85),
        prices=_prices(current=65, high=100, low=55, sma_50=85, rsi=25),
        yoy_pat_growth=25.0,
        yoy_revenue_growth=8.0,
    )
    assert check.qualifies
    assert check.dip is not None
    assert check.dip.divergence_bonus == 10
    assert check.dip.severity == "URGENT"
    assert "earnings divergence" in check.dip.reason


def test_no_divergence_when_revenue_shrinks():
    """Profit up on shrinking revenue is cost-cutting, not a mispricing tell."""
    check = check_dip(
        score=_score(composite=85),
        prices=_prices(current=65, high=100, low=55, sma_50=85, rsi=25),
        yoy_pat_growth=25.0,
        yoy_revenue_growth=-6.0,
    )
    assert check.dip is not None
    assert check.dip.divergence_bonus == 0
    assert check.dip.severity == "ALERT"


def test_rejects_short_price_history():
    """A '52-week high' from three months of closes is not a real drawdown."""
    check = check_dip(score=_score(), prices=_prices(closes=MIN_CLOSES_FOR_DIP - 1))
    assert not check.qualifies
    assert any("daily closes" in r for r in check.reasons)


def test_missing_sma_50_is_not_a_confirmation():
    # Below a 50-day SMA can't be claimed without 50 closes; RSI alone isn't enough.
    check = check_dip(
        score=_score(),
        prices=_prices(current=78, high=100, low=50, sma_50=None, rsi=25),
    )
    assert not check.qualifies
    assert any("technical" in r for r in check.reasons)


def test_dip_rank_key_prefers_urgent_and_divergence():
    urgent = check_dip(
        score=_score(composite=80),
        prices=_prices(current=60, high=100, low=55, sma_50=85, rsi=20),
        yoy_pat_growth=30.0,
    ).dip
    alert = check_dip(
        score=_score(composite=80), prices=_prices()
    ).dip
    assert urgent is not None and alert is not None
    assert dip_rank_key(urgent) > dip_rank_key(alert)


def test_default_min_composite_is_75():
    assert DEFAULT_MIN_COMPOSITE == 75.0
