"""Deterministic valuation pillar — is the price reasonable for what the business earns?

The fundamentals pillar measures business quality and never looks at the
price, so a great company at 80× earnings scored the same as one at 12×.
The first 30-year backtest showed the consequence: forward returns barely
differed across fundamentals bands. This pillar scores the price itself,
on universal curves (one set for every market):

    earnings yield    35   100 / P/E — what each unit of price earns
    FCF yield         35   free cash flow ÷ market value, freshest twelve months
    vs own history    30   today's P/E against the median of its past P/Es

Banks and lenders swap FCF yield for price / book judged against ROE: a
lender's cash flows aren't free cash flow in the industrial sense, and
P/B relative to return on equity is how banks are priced everywhere.

Anti-optimism — the price is set against earnings the business can repeat:
  - A loss-making company has no earnings yield — it scores 0, not "n/a".
  - Unusual items: profit from investment gains and one-off credits is
    taken out (`core_earnings_ratio`, never above 1 — one-off *charges*
    are not added back). A P/E of 15 that is 27 on core earnings is 27.
  - Cyclical peaks: when the core net margin is more than 1.5× the
    company's own median of the last fiscal years, only 1.5× that median
    counts. A memory maker on 9× peak earnings is not cheap.
  - Value-trap guard: in growth-veto territory (both 3y CAGRs below 7%,
    the same test the fundamentals pillar uses) valuation is capped at 70 —
    cheap because shrinking is not a bargain.

Both earnings adjustments apply to the earnings yield and to the P/E set
against its history. Free cash flow is scored as measured: it is cash, and
the trailing window already shows what capex takes out of it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from data_sources.fundamentals_source import BalanceSheetSnapshot, is_bank_snapshot
from scorers.base import Pillar, ScorerResult
from scorers.fundamentals_scorer.industrial import growth_deceleration_cap, pat_growth_label

# A universal hurdle: the return on equity a bank must earn to be worth book.
BANK_COST_OF_EQUITY_PCT = 11.0
MIN_PE_HISTORY = 3
VALUE_TRAP_CAP = 70.0

# Mid-cycle earnings. A margin up to 1.5× the company's own median counts as
# earned — businesses do improve — and anything above is treated as the top
# of a cycle. Six fiscal years span a typical industry cycle (memory, autos,
# commodities turn every 3–5) without anchoring a company to a decade-old
# self; four is the least that makes a median meaningful.
PEAK_MARGIN_ALLOWANCE = 1.5
MARGIN_HISTORY_YEARS = 6
MIN_MARGIN_HISTORY_YEARS = 4
# Unusual items under 5% of profit are ordinary noise (a small FX gain, a
# minor disposal), not a reason to restate the earnings.
NEGLIGIBLE_ONE_OFF_SHARE = 0.05


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


# ── Sustainable earnings: how much of the reported profit can repeat ─────────


@dataclass(frozen=True, slots=True)
class SustainableEarnings:
    """The share of reported profit the valuation counts, and why.

    `core_ratio` removes unusual gains; `cycle_ratio` then caps what is
    left at a mid-cycle margin. Both are 1.0 when there is nothing to
    adjust or nothing known — they only ever lower the earnings counted.
    """

    core_ratio: float
    cycle_ratio: float
    core_margin: float | None  # today's net margin on core earnings, %
    typical_margin: float | None  # the company's own median net margin, %
    margin_years: int  # fiscal years behind `typical_margin`

    @property
    def ratio(self) -> float:
        """Sustainable ÷ reported profit, between 0 and 1."""
        return self.core_ratio * self.cycle_ratio

    @property
    def removes_one_offs(self) -> bool:
        return self.core_ratio < 1.0

    @property
    def above_cycle(self) -> bool:
        return self.cycle_ratio < 1.0

    @property
    def label(self) -> str:
        """What to call the earnings left after the adjustments."""
        parts = [
            name
            for name, applies in (("core", self.removes_one_offs), ("mid-cycle", self.above_cycle))
            if applies
        ]
        return " ".join(parts) or "reported"

    def notes(self) -> list[str]:
        """One evidence line per adjustment that applied."""
        notes: list[str] = []
        if self.removes_one_offs:
            notes.append(
                f"ADJ: {(1 - self.core_ratio) * 100:.0f}% of the profit behind the P/E is "
                f"unusual items (investment gains, one-off credits) — only core earnings count."
            )
        if self.above_cycle and self.typical_margin is not None and self.core_margin is not None:
            if self.typical_margin <= 0:
                notes.append(
                    f"ADJ: the business typically lost money (median net margin "
                    f"{self.typical_margin:.1f}% over {self.margin_years} fiscal years) — "
                    f"today's profit is not yet proven through a cycle and earns no yield credit."
                )
            else:
                notes.append(
                    f"ADJ: net margin {self.core_margin:.1f}% is "
                    f"{self.core_margin / self.typical_margin:.1f}× its {self.margin_years}-year "
                    f"median ({self.typical_margin:.1f}%) — earnings above "
                    f"{PEAK_MARGIN_ALLOWANCE:g}× that median are treated as cyclical, not repeatable."
                )
        return notes


def sustainable_earnings(snap: BalanceSheetSnapshot) -> SustainableEarnings:
    """Public: the early-signal layer and the report read the same adjustments."""
    raw_core = snap.core_earnings_ratio
    if raw_core is None or raw_core >= 1 - NEGLIGIBLE_ONE_OFF_SHARE:
        core_ratio = 1.0
    else:
        core_ratio = max(0.0, raw_core)

    reported_margin = snap.trailing_net_margin
    core_margin = reported_margin * core_ratio if reported_margin is not None else None
    history = snap.net_margin_annual_trend[-MARGIN_HISTORY_YEARS:]
    unknown_cycle = (
        is_bank_snapshot(snap)  # a lender's "margin" on revenue is not an operating margin
        or len(history) < MIN_MARGIN_HISTORY_YEARS
        or core_margin is None
        or core_margin <= 0
    )
    if unknown_cycle:
        return SustainableEarnings(
            core_ratio=core_ratio, cycle_ratio=1.0, core_margin=core_margin,
            typical_margin=None, margin_years=0,
        )

    typical_margin = median(history)
    allowance = PEAK_MARGIN_ALLOWANCE * typical_margin
    cycle_ratio = 1.0 if allowance >= core_margin else max(0.0, allowance) / core_margin
    return SustainableEarnings(
        core_ratio=core_ratio, cycle_ratio=cycle_ratio, core_margin=core_margin,
        typical_margin=typical_margin, margin_years=len(history),
    )


# ── Public entry point ───────────────────────────────────────────────────────


def score_valuation(snap: BalanceSheetSnapshot) -> ScorerResult:
    if snap.quote_unreliable:
        return _unscored(
            "This quote's price and the statements don't reconcile — a depositary receipt "
            "priced per fraction of a share, or statements labelled with the wrong currency — "
            "so no price-based ratio can be trusted. Check the company's primary listing."
        )

    earnings = sustainable_earnings(snap)
    components = _BANK if is_bank_snapshot(snap) else _INDUSTRIAL
    scored: list[tuple[float, int]] = []
    evidence: list[str] = []
    for component in components:
        sub_score, sub_evidence = _SUB_SCORERS[component](snap, earnings)
        if sub_score is not None:
            scored.append((sub_score, _WEIGHTS[component]))
            evidence.extend(sub_evidence)

    if not scored:
        return _unscored("No price-vs-fundamentals data in one currency — valuation unscored.")

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


def _unscored(reason: str) -> ScorerResult:
    return ScorerResult(
        pillar=Pillar.VALUATION,
        score=50.0,
        confidence=0.0,
        evidence=[reason],
        data_completeness_pct=0.0,
    )


# ── Sub-scorers (each returns (score, evidence) or (None, [])) ───────────────


def _earnings_yield(
    snap: BalanceSheetSnapshot, earnings: SustainableEarnings
) -> tuple[float | None, list[str]]:
    profit, window = snap.trailing("net_income")
    if profit is not None and profit < 0:
        return 0.0, [f"Loss-making over the {window.label} — no earnings yield → 0/100"]
    if snap.earnings_yield is None or snap.pe_ratio is None:
        return None, []
    if earnings.ratio <= 0:
        return 0.0, [
            *earnings.notes(),
            f"Reported P/E {snap.pe_ratio:.1f}, but none of that profit is repeatable → 0/100",
        ]
    if earnings.ratio >= 1:
        s = _interpolate(snap.earnings_yield, _EARNINGS_YIELD_CURVE)
        return s, [f"Earnings yield = {snap.earnings_yield:.2f}% (P/E {snap.pe_ratio:.1f}) → {s:.0f}/100"]

    sustainable_yield = snap.earnings_yield * earnings.ratio
    s = _interpolate(sustainable_yield, _EARNINGS_YIELD_CURVE)
    return s, [
        *earnings.notes(),
        f"Earnings yield on {earnings.label} earnings = {sustainable_yield:.2f}% "
        f"(P/E {snap.pe_ratio / earnings.ratio:.1f}; reported P/E {snap.pe_ratio:.1f}) → {s:.0f}/100",
    ]


def _fcf_yield(
    snap: BalanceSheetSnapshot, earnings: SustainableEarnings
) -> tuple[float | None, list[str]]:
    if snap.fcf_yield is None:
        return None, []
    s = 0.0 if snap.fcf_yield < 0 else _interpolate(snap.fcf_yield, _FCF_YIELD_CURVE)
    window = f" over the {snap.cash_flow_window.label}" if snap.cash_flow_window else ""
    return s, [f"FCF yield = {snap.fcf_yield:.2f}%{window} → {s:.0f}/100"]


def _price_to_book(
    snap: BalanceSheetSnapshot, earnings: SustainableEarnings
) -> tuple[float | None, list[str]]:
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


def _vs_history(
    snap: BalanceSheetSnapshot, earnings: SustainableEarnings
) -> tuple[float | None, list[str]]:
    if snap.pe_ratio is None or snap.pe_ratio <= 0 or len(snap.pe_history) < MIN_PE_HISTORY:
        return None, []
    typical = median(snap.pe_history)
    if typical <= 0 or earnings.ratio <= 0:
        return None, []
    pe = snap.pe_ratio / earnings.ratio
    basis = f" on {earnings.label} earnings" if earnings.ratio < 1 else ""
    ratio = pe / typical
    s = _interpolate(ratio, _VS_HISTORY_CURVE)
    return s, [
        f"P/E {pe:.1f}{basis} vs its {len(snap.pe_history)}-year median {typical:.1f} "
        f"({ratio:.2f}×) → {s:.0f}/100"
    ]


_SubScorerFn = Callable[
    [BalanceSheetSnapshot, SustainableEarnings], tuple[float | None, list[str]]
]

_SUB_SCORERS: dict[ValuationComponent, _SubScorerFn] = {
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
        f"(rev {snap.rev_3y_cagr:.1f}%, PAT {pat_growth_label(snap.pat_3y_cagr)}); valuation capped at "
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
