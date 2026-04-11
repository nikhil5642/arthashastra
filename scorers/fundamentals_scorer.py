"""Deterministic fundamentals scorer with graceful degradation.

Takes a sparse BalanceSheetSnapshot; missing components are dropped and
remaining weights rescaled. The result carries data_completeness_pct so
downstream code can tag low-confidence scores.

Two rubrics, chosen automatically by `_is_bank(snap)`:

── Industrial rubric (default) ───────────────────────────────────────

    balance_sheet_health   35   leverage + liquidity + solvency
    profitability          20   ROE / ROCE / ROA
    growth                 15   3y revenue + PAT CAGR
    earnings_quality       15   OCF/PAT + FCF/PAT
    promoter               15   holding + pledge

The balance-sheet bucket is itself a weighted blend of five ratios:
D/E 30% + interest coverage 25% + current ratio 15% + quick ratio 15%
+ net debt / EBITDA 15%.

Vetoes and caps (applied after the weighted sum):
  - Pledge > 10% clamps the final score to 40 (promoter-behaviour veto).
  - Growth deceleration: both 3y CAGRs below 3%/5%/7% → score capped at
    50/60/70 respectively.

── Bank rubric (Financial Services / NBFC / Insurance) ───────────────

Banks are structurally leveraged (10:1 by design) and earn a spread
rather than converting sales to PAT, so the industrial axes make no
sense. We score four bank-appropriate axes:

    profitability          35   ROE + ROA + NIM
    growth                 25   PAT 3y CAGR
    franchise              20   non-interest income ratio + CD ratio
    governance             20   pledge clean (if applicable) + integrity

Curves for ROE / ROA / NIM use bank-calibrated thresholds (ROE 15% is
good for a bank, not a minimum). Credit-Deposit ratio above 100%
drags the franchise score — it means the bank is lending more than it
can fund from sticky deposits.

Bank data limitations: screener.in does not expose GNPA, CASA, or CRAR
in its standard tables, so we cannot score asset quality or capital
adequacy deterministically. Banks are flagged in the report so the
Claude judgment pillar can cover these.
"""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot, is_bank_snapshot
from scorers.base import ScorerResult

_COMPONENT_WEIGHTS: dict[str, int] = {
    "balance_sheet_health": 35,
    "profitability": 20,
    "growth": 15,
    "earnings_quality": 15,
    "promoter": 15,
}

_BANK_COMPONENT_WEIGHTS: dict[str, int] = {
    "bank_profitability": 35,
    "bank_growth": 25,
    "bank_franchise": 20,
    "bank_governance": 20,
}

# Growth-deceleration ceilings — applied only when BOTH revenue and PAT
# CAGRs are populated. (threshold_pct, score_cap) in strictest-first order.
_GROWTH_CEILINGS: tuple[tuple[float, float], ...] = (
    (3.0, 50.0),
    (5.0, 60.0),
    (7.0, 70.0),
)

# ── Public entry point — dispatches to industrial or bank rubric ─────────────


def score_fundamentals(snap: BalanceSheetSnapshot) -> ScorerResult:
    if is_bank_snapshot(snap):
        return _score_bank_fundamentals(snap)
    return _score_industrial_fundamentals(snap)


# ── Industrial rubric ────────────────────────────────────────────────────────


def _score_industrial_fundamentals(snap: BalanceSheetSnapshot) -> ScorerResult:
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

    growth_cap = _growth_deceleration_cap(snap)
    if growth_cap is not None:
        cap_pct, cap_score = growth_cap
        if score > cap_score:
            rev = snap.rev_3y_cagr or 0.0
            pat = snap.pat_3y_cagr or 0.0
            evidence.append(
                f"VETO: 3y growth decelerating (rev {rev:.1f}%, PAT {pat:.1f}%, "
                f"both < {cap_pct:.0f}%) — score capped at {cap_score:.1f} "
                f"(uncapped was {score:.1f}). Balance-sheet quality does not "
                f"compensate for a business that stopped growing."
            )
            score = cap_score

    return ScorerResult(
        pillar="fundamentals",
        score=score,
        confidence=round(total_weight / 100, 2),
        evidence=evidence,
        data_completeness_pct=float(total_weight),
    )


def _growth_deceleration_cap(
    snap: BalanceSheetSnapshot,
) -> tuple[float, float] | None:
    """Return (threshold_pct, score_cap) when both CAGRs are below a threshold."""
    rev = snap.rev_3y_cagr
    pat = snap.pat_3y_cagr
    if rev is None or pat is None:
        return None
    for threshold, cap in _GROWTH_CEILINGS:
        if rev < threshold and pat < threshold:
            return threshold, cap
    return None


# ── Sub-scorers ───────────────────────────────────────────────────────────────


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
    "balance_sheet_health": _score_balance_sheet_health,
    "profitability": _score_profitability,
    "growth": _score_growth,
    "earnings_quality": _score_earnings_quality,
    "promoter": _score_promoter,
}


# ── Bank scoring ──────────────────────────────────────────────────────────────


def _score_bank_fundamentals(snap: BalanceSheetSnapshot) -> ScorerResult:
    components: list[tuple[str, float, int]] = []
    evidence: list[str] = []

    for name, fn in _BANK_SUB_SCORERS.items():
        sub_score, sub_evidence = fn(snap)
        if sub_score is not None:
            components.append((name, sub_score, _BANK_COMPONENT_WEIGHTS[name]))
            evidence.extend(sub_evidence)

    if not components:
        return ScorerResult(
            pillar="fundamentals",
            score=50.0,
            confidence=0.0,
            evidence=["No bank fundamentals available — using neutral prior."],
            data_completeness_pct=0.0,
        )

    total_weight = sum(w for _, _, w in components)
    weighted = sum(s * w for _, s, w in components) / total_weight
    score = round(weighted, 1)

    evidence.insert(
        0,
        "Bank rubric applied (profitability 35% + growth 25% + franchise 20% "
        "+ governance 20%). GNPA / CASA / CRAR are not available from screener "
        "and must be sanity-checked manually before trusting the verdict.",
    )

    return ScorerResult(
        pillar="fundamentals",
        score=score,
        confidence=round(total_weight / 100, 2),
        evidence=evidence,
        data_completeness_pct=float(total_weight),
    )


def _score_bank_profitability(
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


def _score_bank_growth(
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


def _score_bank_franchise(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """Non-interest income strength + Credit-Deposit ratio discipline."""
    sub_scores: list[float] = []
    evidence: list[str] = []

    # Fee / treasury income as % of total income — HDFC Bank runs ~30%+,
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


def _score_bank_governance(
    snap: BalanceSheetSnapshot,
) -> tuple[float | None, list[str]]:
    """Governance proxy for banks.

    Most Indian private banks have zero promoter holding (institutional
    shareholding via mutuals / FIIs / insurers), so the industrial
    promoter-holding curve is inappropriate. We use:
      - pledge clean (95 if 0, scaled down)
      - promoter holding present OR institutional (neutral 70 if 0% —
        no penalty, since it's normal)

    Claude's leadership judgment is where the real governance score lives;
    this sub-score exists mainly to keep the weighted sum sane.
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
            note = "(concentrated — PSU or family-controlled)"
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


_BANK_SUB_SCORERS = {
    "bank_profitability": _score_bank_profitability,
    "bank_growth": _score_bank_growth,
    "bank_franchise": _score_bank_franchise,
    "bank_governance": _score_bank_governance,
}
