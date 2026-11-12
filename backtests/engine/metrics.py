"""Backtest statistics — pure functions over scored observations.

  - Rank IC: Spearman correlation between a score and the next-period
    return across tickers on one date. Averaged over dates, with a t-stat
    saying whether the average is distinguishable from zero.
  - Terciles: each date's tickers split into thirds by score.
  - Verdict bands: forward returns grouped by the verdict a score maps to.
  - Group comparison: on each date both groups appear, one group's mean
    return minus the other's; then a t-stat across dates. One number per
    date, because returns on one date move together — pooling them would
    count one unusual year as many confirmations.
  - Excess return: a return minus its date's average, shown in the tables
    so a group isn't flattered by happening to sit in 2009.

Every ranking function takes the `ScoreKind` to rank by, so the same
statistics answer "does fundamentals work?", "does valuation work?" and
"do they work together?".
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from statistics import median

from scorers.base import VERDICT_BANDS, Verdict, verdict_for
from scorers.composite_scorer import NEUTRAL_PRIOR_SCORE, deterministic_composite

# A rank correlation across a handful of names is mostly noise (its standard
# error is ~1 / √(n − 1): 0.5 for five names), so a date needs this many
# scored tickers with a complete window before it counts in a ranking claim.
MIN_CROSS_SECTION = 10


class ScoreKind(StrEnum):
    FUNDAMENTALS = "fundamentals"
    VALUATION = "valuation"
    COMBINED = "fundamentals + valuation"


@dataclass(slots=True)
class Observation:
    """One ticker scored on one rebalance date."""

    ticker: str
    as_of: date
    is_bank: bool  # scored on the bank / lender rubric
    fundamentals: float
    completeness: float  # % of the fundamentals inputs that were available
    valuation: float | None  # None when no price and statements shared a currency
    forward_return_pct: float | None  # None when the window isn't complete yet
    excess_return_pct: float | None = None  # forward return minus the date's average
    slow_growth: bool = False  # both 3y CAGRs in growth-veto territory
    earnings_yield: float | None = None  # %, 100 / P/E, on reported earnings
    dip_signal: bool = False
    peak_margin: bool = False  # net margin above 1.5× the company's own median
    repeatable_pe: float | None = None  # P/E on the earnings the valuation counts
    material_concerns: int = 0  # ALERT / URGENT early concerns on the as-of date
    material_strengths: int = 0  # ALERT / URGENT early strengths

    @property
    def combined(self) -> float:
        """The deterministic half of the live composite, rescaled to 0–100."""
        return deterministic_composite(self.fundamentals, self.valuation)

    def score(self, kind: ScoreKind) -> float | None:
        if kind is ScoreKind.FUNDAMENTALS:
            return self.fundamentals
        if kind is ScoreKind.VALUATION:
            return self.valuation
        return self.combined


@dataclass(slots=True)
class MeanTest:
    """A sample's mean with its one-sample t-stat against zero."""

    n: int
    mean: float | None
    t_stat: float | None
    positive_share: float | None


@dataclass(slots=True)
class DatedIC:
    as_of: date
    ic: float
    tickers: int


@dataclass(slots=True)
class ReturnStats:
    count: int
    mean_return_pct: float | None
    median_return_pct: float | None  # the typical name — a mean can be one outlier
    mean_excess_pct: float | None
    hit_rate: float | None  # share of observations with a positive return

    @classmethod
    def of(cls, observations: Sequence[Observation]) -> "ReturnStats":
        returns = [o.forward_return_pct for o in observations if o.forward_return_pct is not None]
        excess = [o.excess_return_pct for o in observations if o.excess_return_pct is not None]
        return cls(
            count=len(returns),
            mean_return_pct=_mean(returns),
            median_return_pct=median(returns) if returns else None,
            mean_excess_pct=_mean(excess),
            hit_rate=sum(1 for r in returns if r > 0) / len(returns) if returns else None,
        )


@dataclass(slots=True)
class BandRow:
    verdict: Verdict
    stats: ReturnStats


@dataclass(slots=True)
class GroupComparison:
    """Group A vs group B, compared date by date.

    `by_date` holds one difference per date both groups appear on (A's mean
    return minus B's); the claim is judged on its mean and t-stat, the same
    footing as the rank IC. `a` and `b` are pooled across dates and only
    feed the table.
    """

    a: ReturnStats
    b: ReturnStats
    by_date: MeanTest

    @property
    def difference_pct(self) -> float | None:
        return self.by_date.mean

    @property
    def t_stat(self) -> float | None:
        return self.by_date.t_stat


# ── Preparing observations ───────────────────────────────────────────────────


def attach_excess_returns(observations: Sequence[Observation]) -> None:
    """Set each observation's return minus the average return on its date."""
    for group in _by_date(observations).values():
        average = _mean_return(group)
        for obs in group:
            obs.excess_return_pct = _return(obs) - average


# ── Ranking: does a higher score come before a higher return? ────────────────


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation with average ranks for ties; None if undefined."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def cross_sectional_ics(observations: Sequence[Observation], kind: ScoreKind) -> list[DatedIC]:
    """The rank IC on every date with enough scored tickers whose window is complete."""
    ics: list[DatedIC] = []
    for day, group in sorted(_by_date(_scored(observations, kind)).items()):
        if len(group) < MIN_CROSS_SECTION:
            continue
        ic = spearman([_score(o, kind) for o in group], [_return(o) for o in group])
        if ic is not None:
            ics.append(DatedIC(as_of=day, ic=ic, tickers=len(group)))
    return ics


def largest_cross_section(observations: Sequence[Observation], kind: ScoreKind) -> int:
    """The most scored tickers with a complete window on any one date."""
    return max((len(group) for group in _by_date(_scored(observations, kind)).values()), default=0)


def ic_difference(
    observations: Sequence[Observation], kind: ScoreKind, baseline: ScoreKind
) -> MeanTest:
    """Paired per-date IC(kind) − IC(baseline): does `kind` rank better?"""
    base = {d.as_of: d.ic for d in cross_sectional_ics(observations, baseline)}
    return mean_test(
        [d.ic - base[d.as_of] for d in cross_sectional_ics(observations, kind) if d.as_of in base]
    )


def tercile_spread(observations: Sequence[Observation], kind: ScoreKind) -> MeanTest:
    """Per date: top third's average return minus the bottom third's."""
    spreads: list[float] = []
    for bottom, _, top in _terciles_by_date(observations, kind):
        spreads.append(_mean_return(top) - _mean_return(bottom))
    return mean_test(spreads)


def tercile_returns(observations: Sequence[Observation], kind: ScoreKind) -> list[ReturnStats]:
    """[bottom, middle, top] third, assigned per date and pooled across dates."""
    pooled: tuple[list[Observation], list[Observation], list[Observation]] = ([], [], [])
    for thirds in _terciles_by_date(observations, kind):
        for bucket, members in zip(pooled, thirds):
            bucket.extend(members)
    return [ReturnStats.of(bucket) for bucket in pooled]


def band_returns(observations: Sequence[Observation], kind: ScoreKind) -> list[BandRow]:
    """Forward returns grouped by the verdict band each score falls in."""
    grouped: dict[Verdict, list[Observation]] = {verdict: [] for _, verdict in VERDICT_BANDS}
    for obs in _with_returns(_scored(observations, kind)):
        grouped[verdict_for(_score(obs, kind))].append(obs)
    return [BandRow(verdict=verdict, stats=ReturnStats.of(group)) for verdict, group in grouped.items()]


# ── Comparing two groups ─────────────────────────────────────────────────────


def compare_groups(group_a: Sequence[Observation], group_b: Sequence[Observation]) -> GroupComparison:
    """Per date both groups appear on: A's mean return minus B's; then a t-test across dates."""
    a_by_date, b_by_date = _by_date(group_a), _by_date(group_b)
    differences = [
        _mean_return(a_by_date[day]) - _mean_return(b_by_date[day])
        for day in sorted(a_by_date.keys() & b_by_date.keys())
    ]
    return GroupComparison(
        a=ReturnStats.of(group_a), b=ReturnStats.of(group_b), by_date=mean_test(differences)
    )


def mean_test(values: Sequence[float]) -> MeanTest:
    if not values:
        return MeanTest(n=0, mean=None, t_stat=None, positive_share=None)
    mean = sum(values) / len(values)
    t_stat: float | None = None
    if len(values) >= 2:
        t_stat = _t(mean, math.sqrt(_variance(values) / len(values)))
    positive = sum(1 for v in values if v > 0) / len(values)
    return MeanTest(n=len(values), mean=mean, t_stat=t_stat, positive_share=positive)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _scored(observations: Sequence[Observation], kind: ScoreKind) -> list[Observation]:
    return [o for o in observations if o.score(kind) is not None]


def _score(obs: Observation, kind: ScoreKind) -> float:
    value = obs.score(kind)
    return value if value is not None else NEUTRAL_PRIOR_SCORE


def _with_returns(observations: Sequence[Observation]) -> list[Observation]:
    return [o for o in observations if o.forward_return_pct is not None]


def _return(obs: Observation) -> float:
    return obs.forward_return_pct if obs.forward_return_pct is not None else 0.0


def _mean_return(group: Sequence[Observation]) -> float:
    return sum(_return(o) for o in group) / len(group)


def _by_date(observations: Sequence[Observation]) -> dict[date, list[Observation]]:
    """Observations with a complete forward window, grouped by date."""
    by_date: dict[date, list[Observation]] = defaultdict(list)
    for obs in _with_returns(observations):
        by_date[obs.as_of].append(obs)
    return by_date


def _terciles_by_date(
    observations: Sequence[Observation], kind: ScoreKind
) -> list[tuple[list[Observation], list[Observation], list[Observation]]]:
    thirds: list[tuple[list[Observation], list[Observation], list[Observation]]] = []
    for _, group in sorted(_by_date(_scored(observations, kind)).items()):
        if len(group) < MIN_CROSS_SECTION:
            continue
        ranked = sorted(group, key=lambda o: _score(o, kind))
        size = len(ranked) // 3
        thirds.append((ranked[:size], ranked[size:-size], ranked[-size:]))
    return thirds


def _t(estimate: float, standard_error: float) -> float | None:
    """estimate / standard error; ±inf when every value agreed exactly, None for 0 / 0."""
    if standard_error > 0:
        return estimate / standard_error
    return math.copysign(math.inf, estimate) if estimate != 0 else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _variance(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks
