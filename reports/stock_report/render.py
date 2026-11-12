"""Entry point + markdown renderer for the `analyse <TICKER>` report.

`render_stock_report` is the only public function callers should use.
It composes the header panel, the early-signals panel (concerns and
strengths beyond the scored ratios), the optional business-summary panel,
the Financial Profile panel, the data-quality panel, the five pillar
sections, and the composite verdict box (with any gates that fired) — then
writes a Markdown mirror to disk if `output_dir` is given.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from data_sources.fundamentals_source import BalanceSheetSnapshot
from reports.formatting import (
    GATE_LABELS,
    evidence_table,
    format_money,
    format_price,
    make_console,
    section_panel,
    verdict_text,
)
from reports.stock_report.data_quality import data_quality_panel, data_quality_rows
from reports.stock_report.early_signals import (
    NOTHING_FLAGGED,
    early_signal_rows,
    early_signals_panel,
    freshness_line,
)
from reports.stock_report.financial_profile import (
    financial_profile_panel,
    profile_rows,
)
from reports.stock_report.header import (
    header_panel,
    provisional_note,
    scored_line,
    valuation_parts_for,
)
from scorers.base import Pillar, StockScore
from scorers.composite_scorer import PILLAR_WEIGHTS
from scorers.early_signals import EarlySignal

# Section order and weights come from the composite itself, so the report
# can never disagree with the formula.
PILLAR_WEIGHTS_PCT: tuple[tuple[Pillar, int], ...] = tuple(
    (pillar, round(weight * 100)) for pillar, weight in PILLAR_WEIGHTS.items()
)


def render_stock_report(
    snap: BalanceSheetSnapshot,
    score: StockScore,
    *,
    early_signals: Sequence[EarlySignal] = (),
    output_dir: Path | None = None,
    console: Console | None = None,
) -> Path | None:
    """Print the report to `console` and (optionally) save Markdown to disk."""
    console = console or make_console()

    console.print(header_panel(snap, score))
    console.print(early_signals_panel(snap, early_signals))
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

    quality = data_quality_panel(snap)
    if quality is not None:
        console.print(quality)

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
    provisional = provisional_note(score)
    verdict_body = Group(
        verdict_text(score.verdict, score.confidence),
        *(Text(GATE_LABELS[gate], style="bold red") for gate in score.gates_applied),
        *((Text(provisional, style="yellow"),) if provisional else ()),
    )
    console.print(
        Panel(
            verdict_body,
            title=f"Composite {score.composite}/100",
            title_align="left",
            border_style="bold",
        )
    )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        fp = output_dir / f"{score.ticker}_{datetime.now().strftime('%Y-%m-%d')}.md"
        fp.write_text(_render_markdown(snap, score, early_signals))
        return fp
    return None


# ── Markdown mirror (data_cache/reports/<TICKER>_<date>.md) ──────────────────


def _render_markdown(
    snap: BalanceSheetSnapshot, score: StockScore, early_signals: Sequence[EarlySignal]
) -> str:
    lines: list[str] = [
        f"# {score.ticker} — {snap.company_name or ''}",
        "",
        f"**Verdict:** {score.verdict} (confidence {score.confidence:.0%})",
        f"**Composite:** {score.composite}/100 | {scored_line(score)}"
        + (" | **LOW CONFIDENCE**" if score.low_confidence else ""),
        *((f"_{provisional_note(score)}_",) if score.unscored else ()),
        "",
    ]
    for gate in score.gates_applied:
        lines.append(f"> **{GATE_LABELS[gate]}**")
    if score.gates_applied:
        lines.append("")
    if snap.sector:
        lines.append(f"- Sector/Industry: {snap.sector} / {snap.industry}")
    if snap.exchange or snap.country:
        lines.append(f"- Listing: {snap.exchange or '—'} · {snap.country or '—'}")
    if snap.current_price is not None:
        lines.append(f"- Current price: {format_price(snap.current_price, snap.currency)}")
    if snap.high_52w is not None and snap.low_52w is not None:
        lines.append(
            f"- 52w range: {format_price(snap.low_52w, snap.currency)} – "
            f"{format_price(snap.high_52w, snap.currency)}"
        )
    if snap.market_cap is not None:
        lines.append(
            f"- Market cap: {format_money(snap.market_cap, snap.market_cap_currency)}"
        )
    vparts = valuation_parts_for(snap)
    if vparts:
        lines.append("- Valuation: " + "  ·  ".join(vparts))
    lines.append(f"- Source: {snap.source}")
    lines.append("")

    lines.append("## Early signals")
    lines.extend(f"- {row}" for row in early_signal_rows(early_signals) or [NOTHING_FLAGGED])
    lines.append(f"- _{freshness_line(snap)}_")
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

    quality_rows = data_quality_rows(snap)
    if quality_rows:
        lines.append("## Data quality")
        lines.extend(f"- {row}" for row in quality_rows)
        lines.append("")

    for pillar, weight in PILLAR_WEIGHTS_PCT:
        score_val = getattr(score, f"{pillar}_score")
        lines.append(f"## {pillar.title()} ({weight}%) — {score_val}/100")
        for bullet in score.evidence.get(pillar, []):
            lines.append(f"- {bullet}")
        lines.append("")

    lines.append(f"_Generated: {score.last_updated}_")
    return "\n".join(lines)
