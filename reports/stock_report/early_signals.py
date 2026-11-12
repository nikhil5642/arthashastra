"""Early-signals panel — concerns and strengths, straight under the header.

The pillars below score the latest audited fiscal year. This panel is the
part of the report that looks past it: what the newest quarters, the
latest balance sheet, the ownership tape and the margin history show
(`scorers.early_signals`). Concerns come first, most severe first.

The footer names the periods the signals were read from, so "nothing
flagged" can be told apart from "nothing fresher was published".
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.panel import Panel

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.early_signals import EarlySignal, Tone, of_tone
from signals.signal_bus import Severity

_MARKERS: dict[Tone, str] = {Tone.CONCERN: "▼", Tone.STRENGTH: "▲"}
_STYLES: dict[Tone, str] = {Tone.CONCERN: "red", Tone.STRENGTH: "green"}
_SEVERITY_TAGS: dict[Severity, str] = {Severity.URGENT: "URGENT · ", Severity.ALERT: "", Severity.INFO: ""}

NOTHING_FLAGGED = "Nothing flagged beyond what the pillars already score."


def early_signal_rows(signals: Sequence[EarlySignal]) -> list[str]:
    """Plain-text lines for the Markdown mirror: '▼ URGENT · …', '▲ …'."""
    return [
        f"{_MARKERS[s.tone]} {_SEVERITY_TAGS[s.severity]}{s.message}"
        for tone in Tone
        for s in of_tone(signals, tone)
    ]


def freshness_line(snap: BalanceSheetSnapshot) -> str:
    """Which periods the signals could see."""
    quarters = (
        f"quarters to {snap.quarterly_dates[0]}"
        if snap.quarterly_dates
        else "no quarterly statements published"
    )
    balance = (
        f"balance sheet of {snap.balance_sheet_date}"
        if snap.balance_sheet_date
        else "no year-on-year balance sheet"
    )
    annual = f"fiscal year to {snap.latest_annual_period}" if snap.latest_annual_period else ""
    return "Read from: " + "  ·  ".join(part for part in (quarters, balance, annual) if part)


def early_signals_panel(
    snap: BalanceSheetSnapshot, signals: Sequence[EarlySignal]
) -> Panel:
    lines: list[str] = []
    for tone, heading in ((Tone.CONCERN, "Concerns"), (Tone.STRENGTH, "Strengths")):
        group = of_tone(signals, tone)
        if not group:
            continue
        style = _STYLES[tone]
        lines.append(f"[bold {style}]{heading}[/bold {style}]")
        for signal in group:
            tag = _SEVERITY_TAGS[signal.severity]
            urgent = f"[bold]{tag}[/bold]" if tag else ""
            lines.append(f"  [{style}]{_MARKERS[tone]}[/{style}] {urgent}{signal.message}")
    if not lines:
        lines.append(NOTHING_FLAGGED)
    lines.append(f"[dim]{freshness_line(snap)}[/dim]")

    has_urgent = any(s.severity is Severity.URGENT and s.tone is Tone.CONCERN for s in signals)
    return Panel(
        "\n".join(lines),
        title="Early signals — what changed since the audited numbers",
        title_align="left",
        border_style="red" if has_urgent else "yellow" if signals else "dim",
    )
