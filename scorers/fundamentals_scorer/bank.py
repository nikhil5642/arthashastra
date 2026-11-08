"""Bank / NBFC / levered-financial fundamentals rubric.

Banks are structurally leveraged (10:1 by design) and earn a spread
rather than converting sales to PAT, so the industrial axes make no
sense. We score five bank-appropriate axes:

    profitability   30   ROE + ROA + NIM (bank-calibrated curves)
    asset_quality   25   gross NPA + net NPA + 1y NPA trend
    growth          20   PAT 3y CAGR (revenue CAGR omitted — mergers
                                      inflate it)
    franchise       15   non-interest income ratio + CD ratio
    governance      10   shared ownership sub-score (pledge, insider
                         stake change, neutral-centred holding)

Asset quality carries a quarter of the weight because most bank failures
are credit failures, and a bank can print a fine ROE right up until its
NPAs crystallise. Curves for ROE / ROA / NIM use bank-calibrated
thresholds (ROE 15% is *good* for a bank, not a minimum). A
Credit-Deposit ratio above 100% drags the franchise score — the bank is
lending more than it can fund from sticky deposits.

Data limitations: screener's bank quarterly table exposes Gross / Net
NPA % (scored here), but not CASA or CRAR, so funding mix and capital
adequacy still have to be checked in the Claude judgment.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.base import Pillar, ScorerResult
from scorers.fundamentals_scorer import ownership


class BankComponent(StrEnum):
    """Typed identifiers for the four bank sub-scorers.

    Parallels `IndustrialComponent` — same typo-safety rationale.
    """

    PROFITABILITY = "bank_profitability"
    ASSET_QUALITY = "bank_asset_quality"
    GROWTH = "bank_growth"
    FRANCHISE = "bank_franchise"
    GOVERNANCE = "bank_governance"


_SubScorerFn = Callable[[BalanceSheetSnapshot], tuple[float | None, list[str]]]

_COMPONENT_WEIGHTS: dict[BankComponent, int] = {
    BankComponent.PROFITABILITY: 30,
    BankComponent.ASSET_QUALITY: 25,
    BankComponent.GROWTH: 20,
    BankComponent.FRANCHISE: 15,
    BankComponent.GOVERNANCE: 10,
}


# ── Public entry point ───────────────────────────────────────────────────────


def score(snap: BalanceSheetSnapshot) -> ScorerResult:
    components: list[tuple[BankComponent, float, int]] = []
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
            evidence=["No bank fundamentals available — using neutral prior."],
            data_completeness_pct=0.0,
        )

    total_weight = sum(w for _, _, w in components)
    weighted = sum(s * w for _, s, w in components) / total_weight
    result_score = round(weighted, 1)

    header = (
        "Bank rubric applied (profitability 30% + asset quality 25% + growth 20% "
        "+ franchise 15% + governance 10%). CASA / CRAR are not exposed by the "
        "data sources — check funding mix and capital adequacy manually."
    )
    if not any(name == BankComponent.ASSET_QUALITY for name, _, _ in components):
        header += (
            " No GNPA / NNPA data for this lender — asset quality is unscored "
            "and must be verified manually before trusting the verdict."
        )
    evidence.insert(0, header)

    return ScorerResult(
        pillar=Pillar.FUNDAMENTALS,
        score=result_score,
        confidence=round(total_weight / 100, 2),
        evidence=evidence,
        data_completeness_pct=float(total_weight),
    )


# ── Sub-scorers ──────────────────────────────────────────────────────────────


def _score_profitability(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """ROE + ROA + NIM, each on bank-calibrated curves."""
    sub_scores: list[float] = []
    evidence: list[str] = []

    if snap.roe is not None:
        v = snap.roe
        if v >= 18:
            s = 100.0
        elif v >= 15:
            s = 80.0 + (v - 15) * (20.0 / 3)  # 80 → 100
        elif v >= 12:
            s = 55.0 + (v - 12) * (25.0 / 3)  # 55 → 80
        elif v >= 8:
            s = 25.0 + (v - 8) * (30.0 / 4)  # 25 → 55
        else:
            s = max(0.0, v * 3)
        sub_scores.append(s)
        evidence.append(f"ROE = {v:.1f}% → {s:.0f}/100 (bank curve)")

    if snap.roa is not None:
        v = snap.roa
        if v >= 1.8:
            s = 100.0
        elif v >= 1.5:
            s = 85.0 + (v - 1.5) * 50.0  # 85 → 100
        elif v >= 1.2:
            s = 65.0 + (v - 1.2) * (20.0 / 0.3)  # 65 → 85
        elif v >= 0.9:
            s = 40.0 + (v - 0.9) * (25.0 / 0.3)  # 40 → 65
        elif v >= 0.5:
            s = 15.0 + (v - 0.5) * (25.0 / 0.4)  # 15 → 40
        else:
            s = max(0.0, v * 30)
        sub_scores.append(s)
        evidence.append(f"ROA = {v:.2f}% → {s:.0f}/100 (bank curve)")

    if snap.nim is not None:
        v = snap.nim
        if v >= 4.0:
            s = 100.0
        elif v >= 3.5:
            s = 80.0 + (v - 3.5) * 40.0  # 80 → 100
        elif v >= 3.0:
            s = 55.0 + (v - 3.0) * 50.0  # 55 → 80
        elif v >= 2.5:
            s = 30.0 + (v - 2.5) * 50.0  # 30 → 55
        else:
            s = max(0.0, v * 12)
        sub_scores.append(s)
        evidence.append(f"NIM = {v:.2f}% → {s:.0f}/100 (bank curve)")

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


def _score_asset_quality(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """Gross NPA %, net NPA %, and the 1y gross-NPA trend.

    Thresholds hold across banking systems (gross NPA ≤ 1% is pristine
    anywhere, > 6% is a problem book anywhere), so no market calibration.
    """
    sub_scores: list[float] = []
    evidence: list[str] = []

    if snap.gross_npa_pct is not None:
        g = snap.gross_npa_pct
        if g <= 1.0:
            s = 100.0
        elif g <= 2.0:
            s = 100.0 - (g - 1.0) * 20.0  # 100 → 80
        elif g <= 3.5:
            s = 80.0 - (g - 2.0) * (25.0 / 1.5)  # 80 → 55
        elif g <= 6.0:
            s = 55.0 - (g - 3.5) * 12.0  # 55 → 25
        elif g <= 10.0:
            s = 25.0 - (g - 6.0) * 5.0  # 25 → 5
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"Gross NPA = {g:.2f}% → {s:.0f}/100")

    if snap.net_npa_pct is not None:
        n = snap.net_npa_pct
        if n <= 0.3:
            s = 100.0
        elif n <= 0.75:
            s = 100.0 - (n - 0.3) * (20.0 / 0.45)  # 100 → 80
        elif n <= 1.5:
            s = 80.0 - (n - 0.75) * (25.0 / 0.75)  # 80 → 55
        elif n <= 3.0:
            s = 55.0 - (n - 1.5) * 20.0  # 55 → 25
        elif n <= 5.0:
            s = 25.0 - (n - 3.0) * 10.0  # 25 → 5
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"Net NPA = {n:.2f}% → {s:.0f}/100")

    if snap.gross_npa_delta_1y_pct_points is not None:
        d = snap.gross_npa_delta_1y_pct_points
        if d <= -0.5:
            s, note = 85.0, "improving"
        elif d < 0.25:
            s, note = 70.0, "stable"
        elif d <= 1.0:
            s, note = 40.0, "deteriorating"
        else:
            s, note = 10.0, "sharply deteriorating"
        sub_scores.append(s)
        evidence.append(f"Gross NPA change (1y) = {d:+.2f}pts → {s:.0f}/100 ({note})")

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


def _score_growth(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """PAT 3y CAGR with a bank-appropriate curve.

    Revenue CAGR is intentionally excluded — for post-merger banks
    (HDFCBANK) it's inflated by the step change, and for organic growers
    it tracks PAT CAGR closely anyway. PAT captures real earnings power.
    """
    v = snap.pat_3y_cagr
    if v is None:
        return None, []
    if v >= 18:
        s = 100.0
    elif v >= 12:
        s = 70.0 + (v - 12) * 5.0  # 70 → 100
    elif v >= 8:
        s = 45.0 + (v - 8) * (25.0 / 4)  # 45 → 70
    elif v >= 4:
        s = 20.0 + (v - 4) * (25.0 / 4)  # 20 → 45
    elif v >= 0:
        s = v * 5
    else:
        s = 0.0
    return s, [
        f"PAT 3y CAGR = {v:.1f}% → {s:.0f}/100 "
        f"(bank curve; revenue CAGR omitted — merger step-changes distort it)"
    ]


def _score_franchise(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """Non-interest income strength + Credit-Deposit ratio discipline."""
    sub_scores: list[float] = []
    evidence: list[str] = []

    # Fee / treasury income as % of total income — HDFC Bank runs ~47%,
    # smaller PSU banks often run <15%. A high ratio means franchise
    # diversification and lower NIM sensitivity.
    if snap.non_interest_income_ratio is not None:
        v = snap.non_interest_income_ratio
        if v >= 35:
            s = 100.0
        elif v >= 25:
            s = 75.0 + (v - 25) * 2.5  # 75 → 100
        elif v >= 18:
            s = 50.0 + (v - 18) * (25.0 / 7)  # 50 → 75
        elif v >= 10:
            s = 25.0 + (v - 10) * (25.0 / 8)  # 25 → 50
        else:
            s = max(0.0, v * 2.5)
        sub_scores.append(s)
        evidence.append(
            f"Non-interest income ratio = {v:.1f}% → {s:.0f}/100 "
            f"(fee / treasury share of total income)"
        )

    # CD ratio > 100% = lending more than deposits can fund; stress signal.
    if snap.credit_deposit_ratio is not None:
        v = snap.credit_deposit_ratio
        if v <= 80:
            s = 100.0
        elif v <= 90:
            s = 85.0 - (v - 80) * 1.0  # 85 → 75
        elif v <= 100:
            s = 75.0 - (v - 90) * 2.0  # 75 → 55
        elif v <= 110:
            s = 55.0 - (v - 100) * 3.0  # 55 → 25
        else:
            s = max(0.0, 25.0 - (v - 110) * 2.5)
        sub_scores.append(s)
        evidence.append(
            f"Credit-Deposit ratio = {v:.1f}% → {s:.0f}/100 "
            f"({'stressed post-merger' if v > 100 else 'healthy funding'})"
        )

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


# Governance is the shared ownership sub-score — the same neutral-centred
# logic as the industrial rubric, so a widely-held private bank isn't
# penalised for having no controlling shareholder. Claude's leadership
# judgment is where the real governance read lives.
_SUB_SCORERS: dict[BankComponent, _SubScorerFn] = {
    BankComponent.PROFITABILITY: _score_profitability,
    BankComponent.ASSET_QUALITY: _score_asset_quality,
    BankComponent.GROWTH: _score_growth,
    BankComponent.FRANCHISE: _score_franchise,
    BankComponent.GOVERNANCE: ownership.score,
}
