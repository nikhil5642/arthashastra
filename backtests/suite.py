"""What a backtest suite is, and how its outcome is judged.

A suite tests ONE claim the scoring makes. It reads the shared
observations and returns a `SuiteResult`: an outcome, a one-line finding
with the numbers behind it, and an optional table.

Every suite is judged by the same rules, fixed before looking at data:

    PASS       the effect points the way the claim says, and |t| ≥ 2
    WEAK PASS  it points the claimed way, but |t| < 2 — could be noise
    WEAK FAIL  it points the other way, but |t| < 2 — could be noise
    FAIL       it points the other way, and |t| ≥ 2 — the data contradicts the claim
    NO DATA    too few dates or observations to say anything

Every estimate is one number per date, and the t-stat is taken across
dates. Returns on one date move together, so a date is one piece of
evidence however many tickers it holds.

Most claims take one of two shapes, built here so every suite reads alike:
`ranks_returns` (a higher score comes before a higher return) and
`beats` (one group returns more or less than another on the same dates).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from backtests.engine.metrics import (
    MIN_CROSS_SECTION,
    GroupComparison,
    MeanTest,
    Observation,
    ReturnStats,
    ScoreKind,
    compare_groups,
    cross_sectional_ics,
    largest_cross_section,
    mean_test,
    tercile_returns,
    tercile_spread,
)

SIGNIFICANT_T = 2.0
MIN_DATES = 5  # dates needed to judge a claim: with a rank IC, or with both groups present
MIN_GROUP_SIZE = 10  # observations needed in each group of a comparison

RETURN_COLUMNS: tuple[str, ...] = (
    "Observations", "Mean return", "Median return", "vs same-date average", "Hit rate",
)


class Outcome(StrEnum):
    PASS = "PASS"
    WEAK_PASS = "WEAK PASS"
    WEAK_FAIL = "WEAK FAIL"
    FAIL = "FAIL"
    NO_DATA = "NO DATA"


class Direction(StrEnum):
    """Which sign the claim predicts for its estimate."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(slots=True)
class ResultTable:
    columns: tuple[str, ...]
    rows: list[tuple[str, ...]]


@dataclass(slots=True)
class SuiteResult:
    outcome: Outcome
    finding: str  # the numbers behind the outcome, in one line
    table: ResultTable | None = None


@dataclass(frozen=True, slots=True)
class Suite:
    name: str  # the module name: what you pass to `python3 -m backtests <name>`
    claim: str
    evaluate: Callable[[Sequence[Observation]], SuiteResult]


def judge(
    estimate: float | None, t_stat: float | None, *, claimed: Direction, enough_data: bool
) -> Outcome:
    if not enough_data or estimate is None:
        return Outcome.NO_DATA
    supported = estimate > 0 if claimed is Direction.POSITIVE else estimate < 0
    significant = t_stat is not None and abs(t_stat) >= SIGNIFICANT_T
    if supported:
        return Outcome.PASS if significant else Outcome.WEAK_PASS
    return Outcome.FAIL if significant else Outcome.WEAK_FAIL


# ── Claim shape 1: a higher score comes before a higher return ───────────────


def ranks_returns(observations: Sequence[Observation], kind: ScoreKind) -> SuiteResult:
    """Judged on the mean rank IC; the table shows returns by score tercile."""
    ic = mean_test([d.ic for d in cross_sectional_ics(observations, kind)])
    spread = tercile_spread(observations, kind)
    outcome = judge(ic.mean, ic.t_stat, claimed=Direction.POSITIVE, enough_data=ic.n >= MIN_DATES)
    if ic.n == 0:
        finding = (
            f"a date needs {MIN_CROSS_SECTION}+ scored tickers with a complete forward window; "
            f"the most on any date was {largest_cross_section(observations, kind)}"
        )
    else:
        finding = (
            f"mean rank IC {signed(ic.mean, 3)} over {ic.n} dates (t {signed(ic.t_stat, 2)}, "
            f"positive on {percent(ic.positive_share)}); top third minus bottom third: "
            f"{signed(spread.mean, 1, '%')} per period"
        )
    thirds = zip(("Bottom third", "Middle third", "Top third"), tercile_returns(observations, kind))
    return SuiteResult(
        outcome=outcome,
        finding=finding,
        table=ResultTable(
            columns=(f"{kind.value.capitalize()} score", *RETURN_COLUMNS),
            rows=[(label, *return_cells(stats)) for label, stats in thirds],
        ),
    )


# ── Claim shape 2: one group returns more (or less) than another ─────────────


def beats(
    group: Sequence[Observation],
    rest: Sequence[Observation],
    *,
    claimed: Direction,
    labels: tuple[str, str],
) -> SuiteResult:
    """Judged on the mean per-date difference in return, with its t-stat across dates."""
    comparison = compare_groups(group, rest)
    return SuiteResult(
        outcome=judge_comparison(comparison, claimed=claimed),
        finding=comparison_finding(comparison, labels),
        table=ResultTable(
            columns=("Group", *RETURN_COLUMNS),
            rows=[(labels[0], *return_cells(comparison.a)), (labels[1], *return_cells(comparison.b))],
        ),
    )


def judge_comparison(comparison: GroupComparison, *, claimed: Direction) -> Outcome:
    return judge(
        comparison.difference_pct,
        comparison.t_stat,
        claimed=claimed,
        enough_data=not _shortfall(comparison),
    )


def comparison_finding(comparison: GroupComparison, labels: tuple[str, str]) -> str:
    test = comparison.by_date
    sizes = f"{comparison.a.count} vs {comparison.b.count} observations"
    if test.n == 0:
        return (
            f"no date had both {labels[0].lower()} and {labels[1].lower()} with a complete "
            f"forward window ({sizes})"
        )
    return (
        f"{labels[0]} returned {signed(test.mean, 1, '%')} per period vs {labels[1].lower()} on "
        f"the same date, averaged over {test.n} dates (t {signed(test.t_stat, 2)}, ahead on "
        f"{percent(test.positive_share)}); {sizes}{_shortfall(comparison)}"
    )


def _shortfall(comparison: GroupComparison) -> str:
    """What a comparison lacks to be judged — '' when it has enough."""
    if min(comparison.a.count, comparison.b.count) < MIN_GROUP_SIZE:
        return f" — needs {MIN_GROUP_SIZE}+ observations in each group"
    if comparison.by_date.n < MIN_DATES:
        return f" — needs {MIN_DATES}+ dates with both groups"
    return ""


def mean_test_finding(test: MeanTest, what: str) -> str:
    if test.n == 0:
        return f"no dates to compare {what} on"
    return (
        f"{what}: mean {signed(test.mean, 3)} over {test.n} dates "
        f"(t {signed(test.t_stat, 2)}, positive on {percent(test.positive_share)})"
    )


# ── Table cells ──────────────────────────────────────────────────────────────


def return_cells(stats: ReturnStats) -> tuple[str, str, str, str, str]:
    return (
        str(stats.count),
        signed(stats.mean_return_pct, 1, "%"),
        signed(stats.median_return_pct, 1, "%"),
        signed(stats.mean_excess_pct, 1, "%"),
        percent(stats.hit_rate),
    )


def signed(value: float | None, digits: int, suffix: str = "") -> str:
    return f"{value:+.{digits}f}{suffix}" if value is not None else "—"


def percent(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "—"
