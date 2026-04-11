"""Entry point + markdown renderer for the `analyse <TICKER>` report.

`render_stock_report` is the only public function callers should use.
It composes the header panel, the optional business-summary panel, the
Financial Profile panel, the four pillar sections, and the composite
verdict box — then writes a Markdown mirror to disk if `output_dir` is
given.
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
from reports.stock_report.financial_profile import (
    financial_profile_panel,
    profile_rows,
)
from reports.stock_report.header import (
    header_panel,
    valuation_parts_for,
)
from scorers.base import Pillar, StockScore

# Composite blend: fundamentals is the heaviest pillar (rebalanced from
# leadership 40 / fundamentals 30 after the expanded BS + P&L coverage).
PILLAR_WEIGHTS_PCT: tuple[tuple[Pillar, int], ...] = (
    (Pillar.FUNDAMENTALS, 40),
    (Pillar.LEADERSHIP, 30),
    (Pillar.PROSPECTS, 15),
    (Pillar.NEWS, 15),
)


def render_stock_report(
    snap: BalanceSheetSnapshot,
    score: StockScore,
    *,
    output_dir: Path | None = None,
    console: Console | None = None,
) -> Path | None:
    """Print the report to `console` and (optionally) save Markdown to disk."""
    console = console or make_console()

    console.print(header_panel(snap, score))
    console.print()

    if snap.business_summary:
        console.print(
            Panel(
                snap.business_summary,
                title="Business — what the company actually does",
                title_align="left",
                border_style="dim",
            )
        )
        console.print()

    profile = financial_profile_panel(snap)
    if profile is not None:
        console.print(profile)

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
        fp.write_text(_render_markdown(snap, score))
        return fp
    return None


# ── Markdown mirror (data_cache/reports/<TICKER>_<date>.md) ──────────────────


def _render_markdown(snap: BalanceSheetSnapshot, score: StockScore) -> str:
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
    vparts = valuation_parts_for(snap)
    if vparts:
        lines.append("- Valuation: " + "  ·  ".join(vparts))
    lines.append(f"- Source: {snap.source}")
    lines.append("")

    rows = profile_rows(snap)
    if rows:
        lines.append("## Financial Profile")
        current_section = ""
        for section, metric, value in rows:
            if section and section != current_section:
                lines.append("")
                lines.append(f"**{section}**")
                current_section = section
            lines.append(f"- {metric}: {value}")
        lines.append("")

    for pillar, weight in PILLAR_WEIGHTS_PCT:
        score_val = getattr(score, f"{pillar}_score")
        lines.append(f"## {pillar.title()} ({weight}%) — {score_val}/100")
        for bullet in score.evidence.get(pillar, []):
            lines.append(f"- {bullet}")
        lines.append("")

    lines.append(f"_Generated: {score.last_updated}_")
    return "\n".join(lines)
