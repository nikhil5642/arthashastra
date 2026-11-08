"""Industrial-company fundamentals rubric.

Weights:
    balance_sheet_health   35   leverage + liquidity + solvency
    profitability          20   ROE / ROCE / ROA
    growth                 15   3y revenue + PAT CAGR
    earnings_quality       15   OCF/PAT + FCF/PAT
    ownership              15   pledge + insider stake change + holding
                                (shared with the bank rubric — ownership.py)

The balance-sheet bucket is itself a weighted blend of five ratios:
D/E 30% + interest coverage 25% + current ratio 15% + quick ratio 15%
+ net debt / EBITDA 15%.

Vetoes and caps (applied after the weighted sum):
  - Pledge > 10% clamps the final score to 40 (promoter-behaviour veto).
  - Growth deceleration: both 3y CAGRs below 3%/5%/7% → score capped at
    50/60/70 respectively.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.base import Pillar, ScorerResult
from scorers.fundamentals_scorer import ownership


class IndustrialComponent(StrEnum):
    """Typed identifiers for the five industrial sub-scorers.

    Using a StrEnum here means typos in the weight table surface at
    import time (`AttributeError`) rather than silently zero-weighting
    a component at runtime.
    """

    BALANCE_SHEET_HEALTH = "balance_sheet_health"
    PROFITABILITY = "profitability"
    GROWTH = "growth"
    EARNINGS_QUALITY = "earnings_quality"
    OWNERSHIP = "ownership"


# ── Weights + growth-deceleration ceilings ──────────────────────────────────

_COMPONENT_WEIGHTS: dict[IndustrialComponent, int] = {
    IndustrialComponent.BALANCE_SHEET_HEALTH: 35,
    IndustrialComponent.PROFITABILITY: 20,
    IndustrialComponent.GROWTH: 15,
    IndustrialComponent.EARNINGS_QUALITY: 15,
    IndustrialComponent.OWNERSHIP: 15,
}

# Growth-deceleration ceilings — applied only when BOTH revenue and PAT
# CAGRs are populated. (threshold_pct, score_cap) in strictest-first order.
_GROWTH_CEILINGS: tuple[tuple[float, float], ...] = (
    (3.0, 50.0),
    (5.0, 60.0),
    (7.0, 70.0),
)


# ── Public entry point ───────────────────────────────────────────────────────


def score(snap: BalanceSheetSnapshot) -> ScorerResult:
    components: list[tuple[IndustrialComponent, float, int]] = []
    evidence: list[str] = []

    for name, fn in _SUB_SCORERS.items():
        sub_score, sub_evidence = fn(snap)
        if sub_score is not None:
            components.append((name, sub_score, _COMPONENT_WEIGHTS[name]))
            evidence.extend(sub_evidence)

    if not components:
        return ScorerResult(
            pillar=Pillar.FUNDAMENTALS,
            score=50.0,
            confidence=0.0,
            evidence=["No fundamentals inputs available — using neutral prior."],
            data_completeness_pct=0.0,
        )

    total_weight = sum(w for _, _, w in components)
    weighted = sum(s * w for _, s, w in components) / total_weight
    result_score = round(weighted, 1)

    result_score = _apply_pledge_veto(snap, result_score, evidence)
    result_score = _apply_growth_cap(snap, result_score, evidence)

    return ScorerResult(
        pillar=Pillar.FUNDAMENTALS,
        score=result_score,
        confidence=round(total_weight / 100, 2),
        evidence=evidence,
        data_completeness_pct=float(total_weight),
    )


# ── Vetoes / caps ────────────────────────────────────────────────────────────


def _apply_pledge_veto(
    snap: BalanceSheetSnapshot, score_so_far: float, evidence: list[str]
) -> float:
    if snap.pledge_pct is None or snap.pledge_pct <= 10:
        return score_so_far
    if score_so_far > 40:
        evidence.append(
            f"VETO: promoter pledge {snap.pledge_pct:.1f}% exceeds 10% — "
            f"score capped at 40.0 (uncapped was {score_so_far:.1f})."
        )
        return 40.0
    evidence.append(
        f"VETO: promoter pledge {snap.pledge_pct:.1f}% exceeds 10% "
        f"(score already below 40 on fundamentals)."
    )
    return score_so_far


def _apply_growth_cap(
    snap: BalanceSheetSnapshot, score_so_far: float, evidence: list[str]
) -> float:
    cap = growth_deceleration_cap(snap)
    if cap is None:
        return score_so_far
    cap_pct, cap_score = cap
    if score_so_far <= cap_score:
        return score_so_far
    rev = snap.rev_3y_cagr or 0.0
    pat = snap.pat_3y_cagr or 0.0
    evidence.append(
        f"VETO: 3y growth decelerating (rev {rev:.1f}%, PAT {pat:.1f}%, "
        f"both < {cap_pct:.0f}%) — score capped at {cap_score:.1f} "
        f"(uncapped was {score_so_far:.1f}). Balance-sheet quality does not "
        f"compensate for a business that stopped growing."
    )
    return cap_score


def growth_deceleration_cap(
    snap: BalanceSheetSnapshot,
) -> tuple[float, float] | None:
    """(threshold_pct, score_cap) when both 3y CAGRs are below a threshold, else None.

    Public because the valuation pillar's value-trap guard and the backtest
    suites use the very same definition of "slow grower".
    """
    rev = snap.rev_3y_cagr
    pat = snap.pat_3y_cagr
    if rev is None or pat is None:
        return None
    for threshold, cap in _GROWTH_CEILINGS:
        if rev < threshold and pat < threshold:
            return threshold, cap
    return None


# ── Sub-scorers (each returns (score, evidence_lines) or (None, [])) ─────────


def _score_balance_sheet_health(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """Blend of five balance-sheet ratios with internal weights."""
    sub_scores: list[tuple[float, float]] = []  # (score, weight)
    evidence: list[str] = []

    if snap.debt_to_equity is not None:
        de = snap.debt_to_equity
        if de <= 0.3:
            s = 100.0
        elif de <= 0.5:
            s = 90.0 - (de - 0.3) * 50.0  # 90 → 80
        elif de <= 1.0:
            s = 80.0 - (de - 0.5) * 60.0  # 80 → 50
        elif de <= 1.5:
            s = 50.0 - (de - 1.0) * 50.0  # 50 → 25
        else:
            s = max(0.0, 25.0 - (de - 1.5) * 20.0)
        sub_scores.append((s, 30))
        evidence.append(f"D/E = {de:.2f} → {s:.0f}/100")

    if snap.interest_coverage is not None:
        ic = snap.interest_coverage
        if ic >= 10:
            s = 100.0
        elif ic >= 6:
            s = 85.0 + (ic - 6) * 3.75  # 85 → 100
        elif ic >= 3:
            s = 55.0 + (ic - 3) * 10.0  # 55 → 85
        elif ic >= 1:
            s = (ic - 1) * 27.5  # 0 → 55
        else:
            s = 0.0
        sub_scores.append((s, 25))
        evidence.append(f"Interest coverage = {ic:.2f}× → {s:.0f}/100")

    if snap.current_ratio is not None:
        cr = snap.current_ratio
        if cr >= 2.0:
            s = 100.0
        elif cr >= 1.5:
            s = 85.0 + (cr - 1.5) * 30.0  # 85 → 100
        elif cr >= 1.0:
            s = 55.0 + (cr - 1.0) * 60.0  # 55 → 85
        elif cr >= 0.7:
            s = 25.0 + (cr - 0.7) * 100.0  # 25 → 55
        else:
            s = max(0.0, cr * (25.0 / 0.7))
        sub_scores.append((s, 15))
        evidence.append(f"Current ratio = {cr:.2f} → {s:.0f}/100")

    if snap.quick_ratio is not None:
        qr = snap.quick_ratio
        if qr >= 1.5:
            s = 100.0
        elif qr >= 1.0:
            s = 80.0 + (qr - 1.0) * 40.0  # 80 → 100
        elif qr >= 0.7:
            s = 55.0 + (qr - 0.7) * (25.0 / 0.3)  # 55 → 80
        elif qr >= 0.4:
            s = 25.0 + (qr - 0.4) * (30.0 / 0.3)  # 25 → 55
        else:
            s = max(0.0, qr * (25.0 / 0.4))
        sub_scores.append((s, 15))
        evidence.append(f"Quick ratio = {qr:.2f} → {s:.0f}/100")

    if snap.net_debt_to_ebitda is not None:
        nd = snap.net_debt_to_ebitda
        if nd <= 0:
            s = 100.0  # net cash
        elif nd <= 1.0:
            s = 90.0 - nd * 10.0  # 90 → 80
        elif nd <= 2.5:
            s = 80.0 - (nd - 1.0) * (30.0 / 1.5)  # 80 → 50
        elif nd <= 4.0:
            s = 50.0 - (nd - 2.5) * (30.0 / 1.5)  # 50 → 20
        else:
            s = max(0.0, 20.0 - (nd - 4.0) * 10.0)
        sub_scores.append((s, 15))
        evidence.append(f"Net debt / EBITDA = {nd:.2f}× → {s:.0f}/100")

    if not sub_scores:
        return None, []
    total_w = sum(w for _, w in sub_scores)
    weighted = sum(s * w for s, w in sub_scores) / total_w
    return weighted, evidence


def _score_profitability(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    sub_scores: list[float] = []
    evidence: list[str] = []
    for name, val in (
        ("ROE", snap.roe),
        ("ROCE", snap.roce),
        ("ROA", snap.roa),
    ):
        if val is None:
            continue
        if name == "ROA":
            # ROA scales lower (typically 5–15% for a great business).
            if val >= 12:
                s = 100.0
            elif val >= 8:
                s = 80.0 + (val - 8) * 5.0  # 80 → 100
            elif val >= 5:
                s = 55.0 + (val - 5) * (25.0 / 3)  # 55 → 80
            elif val >= 2:
                s = 25.0 + (val - 2) * 10.0  # 25 → 55
            else:
                s = max(0.0, val * 12.5)
        else:
            if val >= 20:
                s = 100.0
            elif val >= 15:
                s = 80.0 + (val - 15) * 4.0
            elif val >= 10:
                s = 50.0 + (val - 10) * 6.0
            elif val >= 5:
                s = 20.0 + (val - 5) * 6.0
            else:
                s = max(0.0, val * 4)
        sub_scores.append(s)
        evidence.append(f"{name} = {val:.1f}% → {s:.0f}/100")
    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


def _score_growth(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    sub_scores: list[float] = []
    evidence: list[str] = []
    for name, val in (
        ("Revenue 3y CAGR", snap.rev_3y_cagr),
        ("PAT 3y CAGR", snap.pat_3y_cagr),
    ):
        if val is None:
            continue
        if val >= 20:
            s = 100.0
        elif val >= 12:
            s = 70.0 + (val - 12) * 3.75
        elif val >= 6:
            s = 40.0 + (val - 6) * 5.0
        elif val >= 0:
            s = val * (40.0 / 6)
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"{name} = {val:.1f}% → {s:.0f}/100")
    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


def _score_earnings_quality(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """OCF/PAT and FCF/PAT — is reported profit actually turning into cash?"""
    sub_scores: list[float] = []
    evidence: list[str] = []

    if snap.ocf_to_pat is not None:
        r = snap.ocf_to_pat
        if r >= 1.2:
            s = 100.0
        elif r >= 0.8:
            s = 70.0 + (r - 0.8) * 75.0
        elif r >= 0.5:
            s = 30.0 + (r - 0.5) * (40.0 / 0.3)
        elif r >= 0:
            s = r * 60.0
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"OCF/PAT = {r:.2f} → {s:.0f}/100")

    if snap.fcf_to_pat is not None:
        r = snap.fcf_to_pat
        # FCF is stricter — a capex-heavy business can still be good here
        # if it converts at ≥0.5× of PAT over the cycle.
        if r >= 1.0:
            s = 100.0
        elif r >= 0.6:
            s = 75.0 + (r - 0.6) * 62.5  # 75 → 100
        elif r >= 0.3:
            s = 45.0 + (r - 0.3) * 100.0  # 45 → 75
        elif r >= 0:
            s = r * 150.0  # 0 → 45
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"FCF/PAT = {r:.2f} → {s:.0f}/100")

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


_SubScorerFn = Callable[[BalanceSheetSnapshot], tuple[float | None, list[str]]]

_SUB_SCORERS: dict[IndustrialComponent, _SubScorerFn] = {
    IndustrialComponent.BALANCE_SHEET_HEALTH: _score_balance_sheet_health,
    IndustrialComponent.PROFITABILITY: _score_profitability,
    IndustrialComponent.GROWTH: _score_growth,
    IndustrialComponent.EARNINGS_QUALITY: _score_earnings_quality,
    IndustrialComponent.OWNERSHIP: ownership.score,
}
