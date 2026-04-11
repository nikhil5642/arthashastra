"""Bank / NBFC / Financial-Services fundamentals rubric.

Banks are structurally leveraged (10:1 by design) and earn a spread
rather than converting sales to PAT, so the industrial axes make no
sense. We score four bank-appropriate axes:

    profitability   35   ROE + ROA + NIM (bank-calibrated curves)
    growth          25   PAT 3y CAGR (revenue CAGR omitted — mergers
                                      inflate it)
    franchise       20   non-interest income ratio + CD ratio
    governance      20   pledge clean + promoter neutrality

Curves for ROE / ROA / NIM use bank-calibrated thresholds (ROE 15% is
*good* for a bank, not a minimum). Credit-Deposit ratio above 100%
drags the franchise score — it means the bank is lending more than it
can fund from sticky deposits.

Bank data limitations: screener.in does not expose GNPA, CASA, or CRAR
in its standard tables, so we cannot score asset quality or capital
adequacy deterministically. Banks are flagged in the report so the
Claude judgment pillar can cover these.
"""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.base import Pillar, ScorerResult

_COMPONENT_WEIGHTS: dict[str, int] = {
    "bank_profitability": 35,
    "bank_growth": 25,
    "bank_franchise": 20,
    "bank_governance": 20,
}


# ── Public entry point ───────────────────────────────────────────────────────


def score(snap: BalanceSheetSnapshot) -> ScorerResult:
    components: list[tuple[str, float, int]] = []
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

    evidence.insert(
        0,
        "Bank rubric applied (profitability 35% + growth 25% + franchise 20% "
        "+ governance 20%). GNPA / CASA / CRAR are not available from screener "
        "and must be sanity-checked manually before trusting the verdict.",
    )

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


def _score_governance(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """Governance proxy for banks.

    Most Indian private banks have zero promoter holding (institutional
    shareholding via mutuals / FIIs / insurers), so the industrial
    promoter-holding curve is inappropriate. Here:
      - pledge clean (95 if 0, scaled down otherwise),
      - promoter holding at 0% is treated as *neutral* (70) rather than
        penalising as it would in the industrial rubric.

    Claude's leadership judgment is where the real governance score
    lives; this sub-score mainly keeps the weighted sum sane.
    """
    sub_scores: list[float] = []
    evidence: list[str] = []

    if snap.pledge_pct is not None:
        p = snap.pledge_pct
        if p == 0:
            s = 95.0
        elif p <= 5:
            s = 70.0
        elif p <= 10:
            s = 45.0
        elif p <= 25:
            s = 20.0
        else:
            s = 0.0
        sub_scores.append(s)
        evidence.append(f"Promoter pledge = {p:.1f}% → {s:.0f}/100")

    if snap.promoter_holding is not None:
        h = snap.promoter_holding
        if h == 0:
            # Institutional ownership is normal for private banks — no penalty.
            s = 70.0
            note = "(institutional — normal for a private bank)"
        elif h >= 40:
            s = 90.0
            note = "(concentrated promoter — PSU / family / founder-led)"
        else:
            s = 75.0
            note = "(mixed ownership)"
        sub_scores.append(s)
        evidence.append(
            f"Promoter holding = {h:.1f}% → {s:.0f}/100 {note}"
        )

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence


_SUB_SCORERS = {
    "bank_profitability": _score_profitability,
    "bank_growth": _score_growth,
    "bank_franchise": _score_franchise,
    "bank_governance": _score_governance,
}
