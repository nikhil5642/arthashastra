"""Backtest report — the terminal view and its Markdown mirror.

    Settings   tickers, depth, rebalance step, horizon, reporting lag
    Coverage   per ticker: fundamentals source, price history, scored dates
    Suites     per claim: outcome, the numbers behind it, a table
    Summary    one line per suite, like a test run
    Caveats    what the numbers can't tell you
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from backtests.engine.runner import BacktestResult, Coverage
from backtests.suite import Outcome, ResultTable, Suite, SuiteResult

CAVEATS: tuple[str, ...] = (
    "Survivorship: the universe is today's cache — delisted or failed companies are absent, "
    "which flatters every result.",
    "Restatements: screener and Yahoo show today's restated figures for past years; SEC values "
    "are first-filed (as originally reported).",
    "Only the deterministic pillars (fundamentals, valuation) are replayed — Claude's leadership, "
    "prospects and news judgments have no point-in-time history.",
    "Depth is bounded by the sources: SEC ~18 fiscal years (US), screener ~12 (India), Yahoo 4 "
    "(elsewhere). Prices go back further than fundamentals.",
    "Backtest P/E divides by the latest annual EPS (up to ~15 months old); live P/E is trailing "
    "twelve months.",
    "Returns are total returns in each listing's own currency. Group comparisons use the excess "
    "over the same date's average, which removes market-wide moves but not currency moves.",
    "Observations on one date aren't independent, so t-stats are optimistic: |t| ≥ 2 is the "
    "minimum bar, not proof.",
)

OUTCOME_STYLES: dict[Outcome, str] = {
    Outcome.PASS: "bold green",
    Outcome.WEAK: "yellow",
    Outcome.FAIL: "bold red",
    Outcome.NO_DATA: "dim",
}


@dataclass(slots=True)
class SuiteRun:
    suite: Suite
    result: SuiteResult


@dataclass(slots=True)
class BacktestReport:
    settings: str
    result: BacktestResult
    runs: list[SuiteRun]

    @property
    def observations_with_returns(self) -> int:
        return sum(1 for o in self.result.observations if o.forward_return_pct is not None)

    @property
    def any_failed(self) -> bool:
        return any(run.result.outcome is Outcome.FAIL for run in self.runs)

    def tally(self) -> str:
        counts = Counter(run.result.outcome for run in self.runs)
        return " · ".join(f"{counts[o]} {o.value.lower()}" for o in Outcome if counts[o])


# ── Terminal ─────────────────────────────────────────────────────────────────


def render_terminal(console: Console, report: BacktestReport) -> None:
    console.print(
        Panel(
            f"{report.settings}\n"
            f"Scored observations: {len(report.result.observations)}  ·  "
            f"with a complete forward window: {report.observations_with_returns}",
            title="Backtests — do the scores mean what they claim?",
            title_align="left",
        )
    )
    coverage = _coverage_table(report.result.coverage)
    console.print(_rich_table(coverage, title="Coverage (what the data allowed)"))

    for run in report.runs:
        style = OUTCOME_STYLES[run.result.outcome]
        outcome = f"[{style}]{run.result.outcome.value:<8}[/{style}]"
        console.print()
        console.print(f"{outcome} [bold]{run.suite.name}[/bold] — {run.suite.claim}")
        console.print(f"         [dim]{run.result.finding}[/dim]")
        if run.result.table is not None:
            console.print(_rich_table(run.result.table))

    summary = "\n".join(
        f"[{OUTCOME_STYLES[run.result.outcome]}]{run.result.outcome.value:<8}[/] {run.suite.name}"
        for run in report.runs
    )
    console.print(Panel(f"{summary}\n\n{report.tally()}", title="Summary", title_align="left"))
    console.print(
        Panel(
            "\n".join(f"• {c}" for c in CAVEATS),
            title="Caveats", title_align="left", border_style="yellow",
        )
    )


def _rich_table(table: ResultTable, *, title: str | None = None) -> Table:
    rich = Table(title=title)
    for i, column in enumerate(table.columns):
        rich.add_column(column, justify="left" if i == 0 else "right")
    for row in table.rows:
        rich.add_row(*row)
    return rich


# ── Markdown mirror ──────────────────────────────────────────────────────────


def render_markdown(report: BacktestReport) -> str:
    lines = [
        f"# Backtests — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- {report.settings}",
        f"- Scored observations: {len(report.result.observations)} "
        f"(complete forward window: {report.observations_with_returns})",
        f"- Result: {report.tally()}",
        "",
        "## Summary",
        "| Outcome | Suite | Claim |",
        "|---|---|---|",
        *(
            f"| {run.result.outcome.value} | `{run.suite.name}` | {run.suite.claim} |"
            for run in report.runs
        ),
        "",
    ]
    for run in report.runs:
        lines += [
            f"## {run.result.outcome.value} — `{run.suite.name}`",
            "",
            f"**Claim:** {run.suite.claim}",
            "",
            run.result.finding,
            "",
        ]
        if run.result.table is not None:
            lines += [*_markdown_table(run.result.table), ""]
    lines += [
        "## Coverage",
        *_markdown_table(_coverage_table(report.result.coverage)),
        "",
        "## Caveats",
        *(f"- {c}" for c in CAVEATS),
        "",
    ]
    return "\n".join(lines)


def _markdown_table(table: ResultTable) -> list[str]:
    alignment = ["---", *("---:" for _ in table.columns[1:])]
    return [
        f"| {' | '.join(table.columns)} |",
        f"|{'|'.join(alignment)}|",
        *(f"| {' | '.join(row)} |" for row in table.rows),
    ]


# ── Shared ───────────────────────────────────────────────────────────────────


def _coverage_table(coverage: list[Coverage]) -> ResultTable:
    return ResultTable(
        columns=("Ticker", "Fundamentals", "Prices from", "Scored dates", "First", "Last"),
        rows=[
            (
                row.ticker,
                row.fundamentals_source,
                row.price_history_start.isoformat() if row.price_history_start else "—",
                str(row.scored_dates),
                row.first_scored.isoformat() if row.first_scored else "—",
                row.last_scored.isoformat() if row.last_scored else "—",
            )
            for row in coverage
        ],
    )
