"""Rich-based terminal formatting helpers for on-demand reports."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scorers.base import Verdict

# Verdict → Rich style mapping. Keyed by the Verdict enum so typos at
# the call site raise a KeyError instead of falling through to "white".
VERDICT_STYLES: dict[Verdict, str] = {
    Verdict.STRONG_BUY: "bold green",
    Verdict.BUY: "green",
    Verdict.HOLD: "yellow",
    Verdict.AVOID: "red",
    Verdict.RED_FLAG: "bold red",
}


def make_console(*, quiet: bool = False) -> Console:
    if quiet:
        import io
        return Console(file=io.StringIO(), force_terminal=False)
    return Console()


def verdict_text(verdict: Verdict | str, confidence: float) -> Text:
    """Render a verdict with its conviction colour."""
    # Accept raw strings at the boundary (judgment JSON, tests) and
    # coerce back to the enum so downstream is fully typed.
    if not isinstance(verdict, Verdict):
        try:
            verdict = Verdict(verdict)
        except ValueError:
            return Text(f"{verdict}  (confidence {confidence:.0%})", style="white")
    style = VERDICT_STYLES[verdict]
    return Text(f"{verdict.value}  (confidence {confidence:.0%})", style=style)


def evidence_table(evidence: list[str]) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column()
    if not evidence:
        table.add_row("(no evidence)")
    else:
        for bullet in evidence:
            table.add_row(f"• {bullet}")
    return table


def section_panel(title: str, weight_pct: int, score: float, body) -> Panel:
    header = f"{title} ({weight_pct}%) — score {score:.1f}"
    return Panel(body, title=header, title_align="left", border_style="dim")
