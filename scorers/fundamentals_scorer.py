"""Deterministic fundamentals scorer with graceful degradation.

Takes a sparse BalanceSheetSnapshot; missing components are dropped and
remaining weights rescaled. The result carries data_completeness_pct so
downstream code can tag low-confidence scores.

Weights (per PLAN.md §Scoring rubrics):
    debt_health     25
    profitability   25
    growth          20
    cash_quality    15
    promoter        15

Pledge > 10% clamps the final score to 40 (promoter-behaviour veto).
"""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.base import ScorerResult

_COMPONENT_WEIGHTS: dict[str, int] = {
    "debt_health": 25,
    "profitability": 25,
    "growth": 20,
    "cash_quality": 15,
    "promoter": 15,
}


def score_fundamentals(snap: BalanceSheetSnapshot) -> ScorerResult:
    components: list[tuple[str, float, int]] = []
    evidence: list[str] = []

    for name, fn in _SUB_SCORERS.items():
        sub_score, sub_evidence = fn(snap)
        if sub_score is not None:
            components.append((name, sub_score, _COMPONENT_WEIGHTS[name]))
            evidence.extend(sub_evidence)

    if not components:
        return ScorerResult(
            pillar="fundamentals",
            score=50.0,
            confidence=0.0,
            evidence=["No fundamentals inputs available — using neutral prior."],
            data_completeness_pct=0.0,
        )

    total_weight = sum(w for _, _, w in components)
    weighted = sum(s * w for _, s, w in components) / total_weight
    score = round(weighted, 1)

    if snap.pledge_pct is not None and snap.pledge_pct > 10:
        if score > 40:
            evidence.append(
                f"VETO: promoter pledge {snap.pledge_pct:.1f}% exceeds 10% — "
                f"score capped at 40.0 (uncapped was {score:.1f})."
            )
            score = 40.0
        else:
            evidence.append(
                f"VETO: promoter pledge {snap.pledge_pct:.1f}% exceeds 10% "
                f"(score already below 40 on fundamentals)."
            )

    return ScorerResult(
        pillar="fundamentals",
        score=score,
        confidence=round(total_weight / 100, 2),
        evidence=evidence,
        data_completeness_pct=float(total_weight),
    )


# ── Sub-scorers ───────────────────────────────────────────────────────────────


def _score_debt_health(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    sub_scores: list[float] = []
    evidence: list[str] = []

    if snap.debt_to_equity is not None:
        de = snap.debt_to_equity
        if de <= 0.5:
            s = 100.0
        elif de <= 1.0:
            s = 100.0 - (de - 0.5) * 100.0
        elif de <= 1.5:
            s = 50.0 - (de - 1.0) * 50.0
        else:
            s = max(0.0, 25.0 - (de - 1.5) * 20.0)
        sub_scores.append(s)
        evidence.append(f"D/E = {de:.2f} → {s:.0f}/100")

    if snap.interest_coverage is not None:
        ic = snap.interest_coverage
        if ic >= 6:
            s = 100.0
        elif ic >= 3:
            s = 50.0 + (ic - 3) * (50.0 / 3)
        elif ic >= 1:
            s = (ic - 1) * 25.0
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"Interest coverage = {ic:.2f}× → {s:.0f}/100")

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


def _score_profitability(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    sub_scores: list[float] = []
    evidence: list[str] = []
    for name, val in (("ROE", snap.roe), ("ROCE", snap.roce)):
        if val is None:
            continue
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


def _score_cash_quality(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    if snap.ocf_to_pat is None:
        return None, []
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
    return s, [f"OCF/PAT = {r:.2f} → {s:.0f}/100"]


def _score_promoter(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    sub_scores: list[float] = []
    evidence: list[str] = []

    if snap.promoter_holding is not None:
        h = snap.promoter_holding
        if h >= 60:
            s = 100.0
        elif h >= 50:
            s = 85.0 + (h - 50) * 1.5
        elif h >= 40:
            s = 65.0 + (h - 40) * 2.0
        elif h >= 25:
            s = 40.0 + (h - 25) * (25.0 / 15)
        else:
            s = max(0.0, h * 1.6)
        sub_scores.append(s)
        evidence.append(f"Promoter holding = {h:.1f}% → {s:.0f}/100")

    if snap.pledge_pct is not None:
        p = snap.pledge_pct
        if p == 0:
            s = 100.0
        elif p <= 5:
            s = 80.0
        elif p <= 10:
            s = 50.0
        elif p <= 25:
            s = 20.0
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"Promoter pledge = {p:.1f}% → {s:.0f}/100")

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


_SUB_SCORERS = {
    "debt_health": _score_debt_health,
    "profitability": _score_profitability,
    "growth": _score_growth,
    "cash_quality": _score_cash_quality,
    "promoter": _score_promoter,
}
