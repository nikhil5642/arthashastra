"""Dip detection — multi-gate logic with divergence bonus.

A stock becomes a DipSignal only when ALL of these hold:
  1. composite >= min_composite (default 75) AND verdict in allowed_verdicts
  2. current_price <= 0.80 * high_52w (absolute drawdown)
  3. At least 2 of: price < sma_50, rsi14 < 40, price <= 1.05 * low_52w
  4. ticker not in blocked_tickers (recent RED_FLAG from signal bus)

Divergence bonus: fundamentals_score rose >= 5 QoQ while price fell >= 15%
over the same window → +10 to severity ranking.

Severity: drawdown >= 30% + divergence → URGENT, >= 20% → ALERT, else INFO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from scorers.base import StockScore
from screener.price_history import PriceSnapshot

DEFAULT_MIN_COMPOSITE = 75.0
DEFAULT_QUALITY_VERDICTS: frozenset[str] = frozenset({"STRONG_BUY", "BUY"})

VERDICT_ORDER: tuple[str, ...] = (
    "STRONG_BUY",
    "BUY",
    "HOLD",
    "AVOID",
    "RED_FLAG",
)


def allowed_verdicts_from_min(min_verdict: str) -> frozenset[str]:
    """e.g. 'HOLD' → {'STRONG_BUY', 'BUY', 'HOLD'}."""
    if min_verdict not in VERDICT_ORDER:
        raise ValueError(f"Unknown verdict {min_verdict!r}")
    idx = VERDICT_ORDER.index(min_verdict)
    return frozenset(VERDICT_ORDER[: idx + 1])


@dataclass(slots=True)
class DipSignal:
    ticker: str
    current_price: float
    high_52w: float
    drawdown_pct: float
    below_50dma: bool
    rsi14: float
    composite_score: float
    divergence_bonus: int
    reason: str
    severity: str  # INFO | ALERT | URGENT
    triggered_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DipCheck:
    qualifies: bool
    reasons: list[str]
    dip: DipSignal | None


def check_dip(
    *,
    score: StockScore,
    prices: PriceSnapshot,
    blocked_tickers: set[str] | None = None,
    prior_fundamentals_score: float | None = None,
    min_composite: float = DEFAULT_MIN_COMPOSITE,
    allowed_verdicts: frozenset[str] | set[str] | None = None,
) -> DipCheck:
    reasons: list[str] = []
    allowed = allowed_verdicts or DEFAULT_QUALITY_VERDICTS

    blocked = blocked_tickers or set()
    if score.ticker in blocked:
        reasons.append("recent RED_FLAG blocks this ticker")
        return DipCheck(qualifies=False, reasons=reasons, dip=None)

    if score.composite < min_composite:
        reasons.append(
            f"composite {score.composite} below min {min_composite}"
        )
        return DipCheck(qualifies=False, reasons=reasons, dip=None)

    if score.verdict not in allowed:
        reasons.append(f"verdict {score.verdict} not in {sorted(allowed)}")
        return DipCheck(qualifies=False, reasons=reasons, dip=None)

    if prices.high_52w <= 0:
        reasons.append("invalid high_52w")
        return DipCheck(qualifies=False, reasons=reasons, dip=None)

    drawdown_pct = round(
        100 * (prices.high_52w - prices.current_price) / prices.high_52w, 1
    )
    if drawdown_pct < 20:
        reasons.append(f"drawdown {drawdown_pct}% < 20%")
        return DipCheck(qualifies=False, reasons=reasons, dip=None)

    sub_flags: list[str] = []
    if prices.current_price < prices.sma_50:
        sub_flags.append("below 50DMA")
    if prices.rsi14 < 40:
        sub_flags.append(f"RSI14 {prices.rsi14:.0f} < 40")
    if prices.current_price <= 1.05 * prices.low_52w:
        sub_flags.append("near 52w low")

    if len(sub_flags) < 2:
        reasons.append(
            f"only {len(sub_flags)} technical confirmation(s): {sub_flags}"
        )
        return DipCheck(qualifies=False, reasons=reasons, dip=None)

    divergence_bonus = 0
    if prior_fundamentals_score is not None:
        if (
            score.fundamentals_score - prior_fundamentals_score >= 5
            and drawdown_pct >= 15
        ):
            divergence_bonus = 10

    severity = "INFO"
    if drawdown_pct >= 30 and divergence_bonus > 0:
        severity = "URGENT"
    elif drawdown_pct >= 20:
        severity = "ALERT"

    reason_str = (
        f"{drawdown_pct:.1f}% off 52w high; "
        + "; ".join(sub_flags)
        + (f"; divergence +{divergence_bonus}" if divergence_bonus else "")
    )
    dip = DipSignal(
        ticker=score.ticker,
        current_price=prices.current_price,
        high_52w=prices.high_52w,
        drawdown_pct=drawdown_pct,
        below_50dma=prices.current_price < prices.sma_50,
        rsi14=prices.rsi14,
        composite_score=score.composite,
        divergence_bonus=divergence_bonus,
        reason=reason_str,
        severity=severity,
        triggered_at=datetime.now().isoformat(timespec="seconds"),
    )
    reasons.append(f"qualifies: {reason_str}")
    return DipCheck(qualifies=True, reasons=reasons, dip=dip)


def dip_rank_key(dip: DipSignal) -> float:
    severity_weight = {"URGENT": 1.3, "ALERT": 1.0, "INFO": 0.7}
    return (
        dip.composite_score
        * severity_weight.get(dip.severity, 1.0)
        * (1 + dip.divergence_bonus / 100)
    )
