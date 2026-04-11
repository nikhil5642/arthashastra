"""`analyse <TICKER>` — full deep-dive terminal report + Markdown save.

Renders a BalanceSheetSnapshot (live data) + StockScore (deterministic
fundamentals + Claude-written judgments). Pillars that don't yet have a
judgment are shown with a clear "pending Claude judgment" marker so the
reader knows the verdict is incomplete.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from data_sources.fundamentals_source import BalanceSheetSnapshot
from reports.formatting import (
    evidence_table,
    make_console,
    section_panel,
    verdict_text,
)
from scorers.base import StockScore

PILLAR_WEIGHTS_PCT: tuple[tuple[str, int], ...] = (
    ("leadership", 40),
    ("fundamentals", 30),
    ("prospects", 15),
    ("news", 15),
)


def render_stock_report(
    snap: BalanceSheetSnapshot,
    score: StockScore,
    *,
    output_dir: Path | None = None,
    console: Console | None = None,
) -> Path | None:
    console = console or make_console()

    console.print(_header_panel(snap, score))
    console.print()

    for pillar, weight in PILLAR_WEIGHTS_PCT:
        evidence = score.evidence.get(pillar, [])
        score_val = getattr(score, f"{pillar}_score")
        console.print(
            section_panel(
                title=pillar.title(),
                weight_pct=weight,
                score=score_val,
                body=evidence_table(evidence),
            )
        )

    console.print()
    console.print(
        Panel(
            verdict_text(score.verdict, score.confidence),
            title=f"Composite {score.composite}/100",
            title_align="left",
            border_style="bold",
        )
    )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        fp = output_dir / f"{score.ticker}_{datetime.now().strftime('%Y-%m-%d')}.md"
        fp.write_text(_markdown(snap, score))
        return fp
    return None


def _header_panel(snap: BalanceSheetSnapshot, score: StockScore) -> Panel:
    name = snap.company_name or snap.ticker
    sector = f"{snap.sector or '—'} / {snap.industry or '—'}"
    price = f"₹{snap.current_price:,.2f}" if snap.current_price else "—"
    hi = f"₹{snap.high_52w:,.2f}" if snap.high_52w else "—"
    lo = f"₹{snap.low_52w:,.2f}" if snap.low_52w else "—"
    mcap = f"₹{snap.market_cap_cr:,.0f} Cr" if snap.market_cap_cr else "—"

    low_conf_tag = " [yellow](LOW CONFIDENCE)[/yellow]" if score.low_confidence else ""
    source_tag = snap.source or "live"

    lines = [
        f"[bold]{score.ticker}[/bold] — {name}",
        f"[dim]{sector}[/dim]",
        f"Price: {price}  ·  52w: {lo} – {hi}  ·  Market cap: {mcap}",
        f"Data completeness: {score.data_completeness_pct:.0f}%  ·  "
        f"Source: {source_tag}{low_conf_tag}",
    ]
    return Panel("\n".join(lines), title="Stock Analysis", title_align="left")


def _markdown(snap: BalanceSheetSnapshot, score: StockScore) -> str:
    lines: list[str] = [
        f"# {score.ticker} — {snap.company_name or ''}",
        "",
        f"**Verdict:** {score.verdict} (confidence {score.confidence:.0%})",
        f"**Composite:** {score.composite}/100 | "
        f"Data completeness: {score.data_completeness_pct:.0f}%"
        + (" | **LOW CONFIDENCE**" if score.low_confidence else ""),
        "",
    ]
    if snap.sector:
        lines.append(f"- Sector/Industry: {snap.sector} / {snap.industry}")
    if snap.current_price is not None:
        lines.append(f"- Current price: ₹{snap.current_price:,.2f}")
    if snap.high_52w is not None and snap.low_52w is not None:
        lines.append(
            f"- 52w range: ₹{snap.low_52w:,.2f} – ₹{snap.high_52w:,.2f}"
        )
    if snap.market_cap_cr is not None:
        lines.append(f"- Market cap: ₹{snap.market_cap_cr:,.0f} Cr")
    lines.append(f"- Source: {snap.source}")
    lines.append("")

    for pillar, weight in PILLAR_WEIGHTS_PCT:
        score_val = getattr(score, f"{pillar}_score")
        lines.append(f"## {pillar.title()} ({weight}%) — {score_val}/100")
        for bullet in score.evidence.get(pillar, []):
            lines.append(f"- {bullet}")
        lines.append("")

    lines.append(f"_Generated: {score.last_updated}_")
    return "\n".join(lines)
