"""Deterministic valuation pillar — is the price reasonable for what the business earns?

The fundamentals pillar measures business quality and never looks at the
price, so a great company at 80× earnings scored the same as one at 12×.
The first 30-year backtest showed the consequence: forward returns barely
differed across fundamentals bands. This pillar scores the price itself,
on universal curves (one set for every market):

    earnings yield    35   100 / P/E — what each unit of price earns
    FCF yield         35   free cash flow per share ÷ price
    vs own history    30   today's P/E against the median of its past P/Es

Banks and lenders swap FCF yield for price / book judged against ROE: a
lender's cash flows aren't free cash flow in the industrial sense, and
P/B relative to return on equity is how banks are priced everywhere.

Anti-optimism:
  - A loss-making company has no earnings yield — it scores 0, not "n/a".
  - Value-trap guard: in growth-veto territory (both 3y CAGRs below 7%,
    the same test the fundamentals pillar uses) valuation is capped at 70 —
    cheap because shrinking is not a bargain.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum
from statistics import median

from data_sources.fundamentals_source import BalanceSheetSnapshot, is_bank_snapshot
from scorers.base import Pillar, ScorerResult
from scorers.fundamentals_scorer.industrial import growth_deceleration_cap

# A universal hurdle: the return on equity a bank must earn to be worth book.
BANK_COST_OF_EQUITY_PCT = 11.0
MIN_PE_HISTORY = 3
VALUE_TRAP_CAP = 70.0


class ValuationComponent(StrEnum):
    EARNINGS_YIELD = "earnings_yield"
    FCF_YIELD = "fcf_yield"
    PRICE_TO_BOOK = "price_to_book"
    VS_HISTORY = "vs_history"


_WEIGHTS: dict[ValuationComponent, int] = {
    ValuationComponent.EARNINGS_YIELD: 35,
    ValuationComponent.FCF_YIELD: 35,
    ValuationComponent.PRICE_TO_BOOK: 35,
    ValuationComponent.VS_HISTORY: 30,
}

_INDUSTRIAL: tuple[ValuationComponent, ...] = (
    ValuationComponent.EARNINGS_YIELD,
    ValuationComponent.FCF_YIELD,
    ValuationComponent.VS_HISTORY,
)
_BANK: tuple[ValuationComponent, ...] = (
    ValuationComponent.EARNINGS_YIELD,
    ValuationComponent.PRICE_TO_BOOK,
    ValuationComponent.VS_HISTORY,
)

# Curves as (input, score) breakpoints, linear in between, flat beyond the ends.
_EARNINGS_YIELD_CURVE = ((0.0, 0.0), (1.0, 15.0), (2.5, 40.0), (4.0, 65.0), (6.0, 85.0), (8.0, 100.0))
_FCF_YIELD_CURVE = ((0.0, 10.0), (1.5, 35.0), (3.0, 60.0), (5.0, 85.0), (7.0, 100.0))
# Lower is cheaper for both ratio curves below.
_VS_HISTORY_CURVE = ((0.7, 100.0), (0.85, 80.0), (1.0, 60.0), (1.25, 40.0), (1.6, 10.0), (2.1, 0.0))
_PB_VS_JUSTIFIED_CURVE = ((0.7, 100.0), (1.0, 70.0), (1.5, 40.0), (2.0, 15.0), (3.0, 0.0))


def score_valuation(snap: BalanceSheetSnapshot) -> ScorerResult:
    components = _BANK if is_bank_snapshot(snap) else _INDUSTRIAL
    scored: list[tuple[float, int]] = []
    evidence: list[str] = []
    for component in components:
        sub_score, sub_evidence = _SUB_SCORERS[component](snap)
        if sub_score is not None:
            scored.append((sub_score, _WEIGHTS[component]))
            evidence.extend(sub_evidence)

    if not scored:
        return ScorerResult(
            pillar=Pillar.VALUATION,
            score=50.0,
            confidence=0.0,
            evidence=["No price-vs-fundamentals data in one currency — valuation unscored."],
            data_completeness_pct=0.0,
        )

    total_weight = sum(weight for _, weight in scored)
    result = round(sum(s * w for s, w in scored) / total_weight, 1)
    result = _apply_value_trap_cap(snap, result, evidence)
    return ScorerResult(
        pillar=Pillar.VALUATION,
        score=result,
        confidence=round(total_weight / 100, 2),
        evidence=evidence,
        data_completeness_pct=float(total_weight),
    )


# ── Sub-scorers (each returns (score, evidence) or (None, [])) ───────────────


def _earnings_yield(snap: BalanceSheetSnapshot) -> tuple[float | None, list[str]]:
    if snap.net_income is not None and snap.net_income < 0:
        return 0.0, ["Loss-making — no earnings yield → 0/100"]
    if snap.earnings_yield is None or snap.pe_ratio is None:
        return None, []
    s = _interpolate(snap.earnings_yield, _EARNINGS_YIELD_CURVE)
    return s, [f"Earnings yield = {snap.earnings_yield:.2f}% (P/E {snap.pe_ratio:.1f}) → {s:.0f}/100"]


def _fcf_yield(snap: BalanceSheetSnapshot) -> tuple[float | None, list[str]]:
    if snap.fcf_yield is None:
        return None, []
    s = 0.0 if snap.fcf_yield < 0 else _interpolate(snap.fcf_yield, _FCF_YIELD_CURVE)
    return s, [f"FCF yield = {snap.fcf_yield:.2f}% → {s:.0f}/100"]


def _price_to_book(snap: BalanceSheetSnapshot) -> tuple[float | None, list[str]]:
    if snap.price_to_book is None or snap.roe is None:
        return None, []
    if snap.roe <= 0:
        return 0.0, [f"P/B {snap.price_to_book:.2f} with ROE {snap.roe:.1f}% — no return on book → 0/100"]
    justified = snap.roe / BANK_COST_OF_EQUITY_PCT
    s = _interpolate(snap.price_to_book / justified, _PB_VS_JUSTIFIED_CURVE)
    return s, [
        f"P/B = {snap.price_to_book:.2f} vs justified {justified:.2f} "
        f"(ROE {snap.roe:.1f}% ÷ {BANK_COST_OF_EQUITY_PCT:.0f}% cost of equity) → {s:.0f}/100"
    ]


def _vs_history(snap: BalanceSheetSnapshot) -> tuple[float | None, list[str]]:
    if snap.pe_ratio is None or snap.pe_ratio <= 0 or len(snap.pe_history) < MIN_PE_HISTORY:
        return None, []
    typical = median(snap.pe_history)
    if typical <= 0:
        return None, []
    ratio = snap.pe_ratio / typical
    s = _interpolate(ratio, _VS_HISTORY_CURVE)
    return s, [
        f"P/E {snap.pe_ratio:.1f} vs its {len(snap.pe_history)}-year median {typical:.1f} "
        f"({ratio:.2f}×) → {s:.0f}/100"
    ]


_SUB_SCORERS: dict[
    ValuationComponent, Callable[[BalanceSheetSnapshot], tuple[float | None, list[str]]]
] = {
    ValuationComponent.EARNINGS_YIELD: _earnings_yield,
    ValuationComponent.FCF_YIELD: _fcf_yield,
    ValuationComponent.PRICE_TO_BOOK: _price_to_book,
    ValuationComponent.VS_HISTORY: _vs_history,
}


# ── Guards + curve helper ────────────────────────────────────────────────────


def _apply_value_trap_cap(snap: BalanceSheetSnapshot, score: float, evidence: list[str]) -> float:
    slow = growth_deceleration_cap(snap)
    if slow is None or score <= VALUE_TRAP_CAP:
        return score
    evidence.append(
        f"CAP: value-trap guard — 3y revenue and PAT growth both below {slow[0]:.0f}% "
        f"(rev {snap.rev_3y_cagr:.1f}%, PAT {snap.pat_3y_cagr:.1f}%); valuation capped at "
        f"{VALUE_TRAP_CAP:.0f} (uncapped {score:.1f}). Cheap because shrinking is not a bargain."
    )
    return VALUE_TRAP_CAP


def _interpolate(x: float, points: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear score through `points`, clamped at both ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    return points[-1][1]
