"""Dip detection — a quality stock the market has marked down.

A stock becomes a DipSignal only when ALL of these hold:
  1. it isn't blocked (integrity veto)
  2. composite >= min_composite (default 75) AND verdict in allowed_verdicts
  3. at least MIN_CLOSES_FOR_DIP daily closes (~6 months) — a "52-week
     high" drawn from a few weeks of history is meaningless
  4. current price <= 80% of the 52-week high
  5. at least 2 technical confirmations: below the 50-day SMA, RSI14 < 40,
     within 5% of the 52-week low

Earnings divergence (+10 to the ranking): the latest quarter's PAT grew
>= 15% year-on-year, with revenue not shrinking, even though the price
fell. Earnings and price moving in opposite directions is the classic
mispricing tell.

Severity: drawdown >= 30% with earnings divergence → URGENT, else ALERT.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime

from scorers.base import StockScore, Verdict
from screener.price_history import PriceSnapshot
from signals.signal_bus import Severity

DEFAULT_MIN_COMPOSITE = 75.0
DEFAULT_QUALITY_VERDICTS: frozenset[Verdict] = frozenset({Verdict.STRONG_BUY, Verdict.BUY})

MIN_CLOSES_FOR_DIP = 120
MIN_DRAWDOWN_PCT = 20.0
URGENT_DRAWDOWN_PCT = 30.0
RSI_OVERSOLD = 40.0
NEAR_LOW_BAND = 1.05  # within 5% of the 52-week low
DIVERGENCE_MIN_PAT_GROWTH_PCT = 15.0
DIVERGENCE_BONUS = 10

# Verdicts from best to worst. Driven off the `Verdict` enum's definition
# order so there's only one canonical list of valid values project-wide.
VERDICT_ORDER: tuple[Verdict, ...] = tuple(Verdict)


def allowed_verdicts_from_min(min_verdict: Verdict | str) -> frozenset[Verdict]:
    """e.g. `Verdict.HOLD` → {STRONG_BUY, BUY, HOLD}.

    Accepts either a `Verdict` enum or a raw string for CLI args.
    """
    try:
        cutoff = Verdict(min_verdict)
    except ValueError as exc:
        raise ValueError(f"Unknown verdict {min_verdict!r}") from exc
    idx = VERDICT_ORDER.index(cutoff)
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
    severity: Severity
    triggered_at: str

    def to_dict(self) -> dict[str, object]:
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
    yoy_pat_growth: float | None = None,
    yoy_revenue_growth: float | None = None,
    min_composite: float = DEFAULT_MIN_COMPOSITE,
    allowed_verdicts: Iterable[Verdict] | None = None,
) -> DipCheck:
    # Explicit None check — an empty collection is a legitimate "block all".
    allowed = (
        DEFAULT_QUALITY_VERDICTS if allowed_verdicts is None else frozenset(allowed_verdicts)
    )

    if score.ticker in (blocked_tickers or set()):
        return _reject("blocked: integrity veto")
    if score.composite < min_composite:
        return _reject(f"composite {score.composite} below min {min_composite}")
    if score.verdict not in allowed:
        return _reject(f"verdict {score.verdict} not in {sorted(allowed)}")
    if prices.closes_count < MIN_CLOSES_FOR_DIP:
        return _reject(
            f"only {prices.closes_count} daily closes — need {MIN_CLOSES_FOR_DIP} "
            f"for a meaningful 52-week range"
        )
    if prices.high_52w <= 0:
        return _reject("invalid high_52w")

    drawdown_pct = round(
        100 * (prices.high_52w - prices.current_price) / prices.high_52w, 1
    )
    if drawdown_pct < MIN_DRAWDOWN_PCT:
        return _reject(f"drawdown {drawdown_pct}% < {MIN_DRAWDOWN_PCT:.0f}%")

    confirmations = _technical_confirmations(prices)
    if len(confirmations) < 2:
        return _reject(
            f"only {len(confirmations)} technical confirmation(s): {confirmations}"
        )

    diverging = _earnings_diverge(yoy_pat_growth, yoy_revenue_growth)
    divergence_bonus = DIVERGENCE_BONUS if diverging else 0
    severity = (
        Severity.URGENT
        if diverging and drawdown_pct >= URGENT_DRAWDOWN_PCT
        else Severity.ALERT
    )

    reason_parts = [f"{drawdown_pct:.1f}% off 52w high", *confirmations]
    if diverging and yoy_pat_growth is not None:
        reason_parts.append(f"earnings divergence: PAT {yoy_pat_growth:+.0f}% YoY")
    reason = "; ".join(reason_parts)

    dip = DipSignal(
        ticker=score.ticker,
        current_price=prices.current_price,
        high_52w=prices.high_52w,
        drawdown_pct=drawdown_pct,
        below_50dma=_below_sma_50(prices),
        rsi14=prices.rsi14,
        composite_score=score.composite,
        divergence_bonus=divergence_bonus,
        reason=reason,
        severity=severity,
        triggered_at=datetime.now().isoformat(timespec="seconds"),
    )
    return DipCheck(qualifies=True, reasons=[f"qualifies: {reason}"], dip=dip)


# ── Ranking ──────────────────────────────────────────────────────────────────

_SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.URGENT: 1.3,
    Severity.ALERT: 1.0,
}


def dip_rank_key(dip: DipSignal) -> float:
    return (
        dip.composite_score
        * _SEVERITY_WEIGHT.get(dip.severity, 1.0)
        * (1 + dip.divergence_bonus / 100)
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _reject(reason: str) -> DipCheck:
    return DipCheck(qualifies=False, reasons=[reason], dip=None)


def _below_sma_50(prices: PriceSnapshot) -> bool:
    return prices.sma_50 is not None and prices.current_price < prices.sma_50


def _technical_confirmations(prices: PriceSnapshot) -> list[str]:
    confirmations: list[str] = []
    if _below_sma_50(prices):
        confirmations.append("below 50DMA")
    if prices.rsi14 < RSI_OVERSOLD:
        confirmations.append(f"RSI14 {prices.rsi14:.0f} < {RSI_OVERSOLD:.0f}")
    if prices.current_price <= NEAR_LOW_BAND * prices.low_52w:
        confirmations.append("near 52w low")
    return confirmations


def _earnings_diverge(
    yoy_pat_growth: float | None, yoy_revenue_growth: float | None
) -> bool:
    """Profit growing strongly while revenue isn't shrinking."""
    if yoy_pat_growth is None or yoy_pat_growth < DIVERGENCE_MIN_PAT_GROWTH_PCT:
        return False
    return yoy_revenue_growth is None or yoy_revenue_growth >= 0
