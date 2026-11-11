"""Analysis-context panels shown at the top of every `analyse` run.

The banner tells the reader (a) what data adapters fired, (b) whether
the price history was refreshed, (c) how fresh the statements are and
whether the sources disagreed, (d) whether this is a bank (different
rubric), and (e) whether there's a cached Claude judgment and how stale
it is. This is the "honesty layer" of the report — it makes explicit
which pillars are deterministic-only vs blended with Claude.

A second panel says what changed since the previous look at the ticker:
a verdict that moved, and early concerns or strengths that are new.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.panel import Panel

from data_sources.fundamentals_source import is_bank_snapshot
from fundamental_engine.judgments import age_in_days
from reports.stock_report.data_quality import data_quality_summary

if TYPE_CHECKING:
    from fundamental_engine.analyse_pipeline import AnalysisResult
    from fundamental_engine.tracking import RecordedAnalysis


def analysis_context_panel(
    result: "AnalysisResult", ticker: str, price_rows_written: int
) -> Panel:
    """Build the 'Analysis context — <TICKER>' banner panel."""
    snap = result.snapshot
    lines: list[str] = [
        f"Fundamentals:     live fetch  ·  source: {snap.source}  ·  "
        f"completeness {snap.data_completeness_pct:.0f}%"
    ]

    if price_rows_written > 0:
        lines.append(
            f"Price history:    refreshed  ·  {price_rows_written} daily closes "
            f"persisted to prices.db"
        )
    else:
        lines.append(
            "Price history:    not refreshed this run "
            "(empty yfinance history response)"
        )

    lines.append(f"Data quality:     {data_quality_summary(snap)}")

    if is_bank_snapshot(snap):
        lines.append(
            "[cyan]Bank rubric:     profitability (ROE + ROA + NIM) + asset "
            "quality (gross / net NPA) + PAT growth + franchise (fee income + "
            "CD ratio) + governance. CASA / CRAR are not exposed by screener — "
            "verify funding mix and capital adequacy in the Claude judgment "
            "before trusting the verdict.[/cyan]"
        )

    lines.append(_judgment_line(result))

    return Panel(
        "\n".join(lines),
        title=f"Analysis context — {ticker}",
        title_align="left",
        border_style="dim",
    )


def changes_panel(recorded: "RecordedAnalysis") -> Panel | None:
    """'Since the last look' — None on a first analysis or when nothing moved."""
    previous = recorded.previous
    if previous is None or not recorded.changes:
        return None
    since = previous.score.last_updated[:10] or "the last look"
    delta = recorded.current.score.composite - previous.score.composite
    lines = [f"Composite {previous.score.composite:.1f} → "
             f"{recorded.current.score.composite:.1f} ({delta:+.1f})"]
    lines.extend(f"• {change.message}" for change in recorded.changes)
    return Panel(
        "\n".join(lines),
        title=f"Changed since {since}",
        title_align="left",
        border_style="yellow",
    )


# ── Judgment age helpers (also used by the CLI "STALE" warning hint) ─────────


def judgment_age_days(updated_at: str) -> int | None:
    """Days since a judgment's `updated_at`, or None when it doesn't parse."""
    return age_in_days(updated_at)


def format_age(days: int | None) -> str:
    if days is None:
        return "age unknown"
    if days == 0:
        return "0 days old"
    if days == 1:
        return "1 day old"
    return f"{days} days old"


# ── Internals ────────────────────────────────────────────────────────────────


def _judgment_line(result: "AnalysisResult") -> str:
    if result.judgment is None:
        return (
            "Cached judgment:  [red]none[/red]  ·  leadership / prospects / "
            "news will be rendered as pending — this is the honest "
            "deterministic-only baseline."
        )
    age = judgment_age_days(result.judgment.updated_at)
    age_str = format_age(age)
    tag = (
        f"[yellow]{age_str} — STALE[/yellow]"
        if age is not None and age >= 3
        else f"[green]{age_str}[/green]"
    )
    leader = result.judgment.leader_name or "—"
    return (
        f"Cached judgment:  {tag}  ·  leader: {leader}  ·  "
        f"last updated {result.judgment.updated_at or 'unknown'}"
    )
