"""Ownership & alignment sub-score — shared by the industrial and bank rubrics.

Scores what ownership data actually says about alignment and risk, rather
than "a bigger insider stake is always better" (the old promoter curve gave
a widely-held company like Infosys or Apple close to zero):

  - Pledged shares — the one hard risk signal. Scored only when a source
    actually reported it; an unknown pledge is never treated as 0%.
  - Insider / promoter stake change over the last year — selling and
    buying are both signals, an unchanged stake is neutral.
  - Holding level — neutral-centred. Widely-held, professionally-run
    companies are normal (65), a meaningful insider stake earns a modest
    alignment premium, and nothing here can reward concentration enough
    to mask a pledge or insider-selling problem.

Each piece is optional; the sub-score averages whichever are present and
returns None when none are, so the parent rubric drops the component.
"""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot


def score(snap: BalanceSheetSnapshot) -> tuple[float | None, list[str]]:
    sub_scores: list[float] = []
    evidence: list[str] = []

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
        evidence.append(f"Pledged shares = {p:.1f}% → {s:.0f}/100")

    if snap.insider_delta_1y_pct_points is not None:
        d = snap.insider_delta_1y_pct_points
        if d >= 3:
            s, note = 95.0, "insider accumulation"
        elif d >= 1:
            s, note = 85.0, "insider buying"
        elif d > -1:
            s, note = 65.0, "stake unchanged"
        elif d > -3:
            s, note = 40.0, "insider selling"
        else:
            s, note = 15.0, "heavy insider selling"
        sub_scores.append(s)
        evidence.append(
            f"Insider / promoter stake change (1y) = {d:+.2f}pts → {s:.0f}/100 ({note})"
        )

    if snap.insider_holding is not None:
        h = snap.insider_holding
        if h >= 50:
            s, note = 85.0, "controlling insider stake"
        elif h >= 25:
            s, note = 75.0, "meaningful insider stake"
        else:
            s, note = 65.0, "widely held — neutral, not a penalty"
        sub_scores.append(s)
        evidence.append(f"Insider / promoter holding = {h:.1f}% → {s:.0f}/100 ({note})")

    if not sub_scores:
        return None, []
    return sum(sub_scores) / len(sub_scores), evidence
