"""`brief` — market overview from cached state.

Reads cached scores, signals, and dip candidates and prints a structured
summary. No LLM narrative — Claude produces the commentary in the
`/market-brief` skill after reading this deterministic baseline.

Every piece of data this command touches is loaded as a typed object
(`ExtendedScore`, `DipCandidateRecord`, `Signal`) — there are no raw
JSON-dict lookups with fallback defaults anywhere in this file.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from rich.panel import Panel
from rich.table import Table

from fundamental_engine.config import AnalystConfig
from fundamental_engine.dip_candidate import DipCandidateRecord
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.universe import load_dip_candidates, load_scores
from reports.formatting import make_console
from signals.signal_bus import Signal, SignalBus, SignalType

log = logging.getLogger(__name__)


def run_brief_command() -> int:
    cfg = AnalystConfig.from_env()
    console = make_console()
    cache_dir = cfg.data_cache_dir

    scores = load_scores(cache_dir)
    dip_candidates = load_dip_candidates(cache_dir)
    bus = SignalBus(path=cache_dir / "signals.json")
    signals = bus.read_all()

    sector_counter = Counter(
        extended.sector
        for extended in scores.values()
        if extended.sector and extended.sector != "Unknown"
    )

    red_flags = [
        s for s in signals if s.signal_type == SignalType.LEADERSHIP_RED_FLAG
    ]
    score_updates = [s for s in signals if s.signal_type == SignalType.SCORE_UPDATED]

    console.print(
        Panel(
            f"[bold]Market brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}[/bold]\n"
            f"Cached universe: {len(scores)} tickers  ·  "
            f"Dip candidates: {len(dip_candidates)}  ·  "
            f"Signals logged: {len(signals)}",
            title_align="left",
        )
    )

    _render_top_by_composite(console, scores)
    _render_dip_candidates(console, dip_candidates)
    _render_sector_coverage(console, sector_counter)
    _render_red_flags(console, red_flags)
    _render_score_updates(console, score_updates)

    reports_dir = cache_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    fp = reports_dir / f"brief_{datetime.now().strftime('%Y-%m-%d')}.md"
    fp.write_text(
        _render_markdown(scores, dip_candidates, red_flags, sector_counter)
    )
    console.print(f"\n[dim]Report saved: {fp}[/dim]")

    if not scores:
        console.print(
            "\n[yellow]The universe is empty. Run `fundamental_engine "
            "refresh <TICKER>` on a few stocks to populate the cache.[/yellow]"
        )
    return 0


# ── Terminal panels ─────────────────────────────────────────────────────────


def _render_top_by_composite(
    console, scores: dict[str, ExtendedScore]
) -> None:
    if not scores:
        return
    ranked = sorted(
        scores.values(), key=lambda e: e.score.composite, reverse=True
    )
    top_table = Table(title="Top by composite")
    top_table.add_column("Ticker", style="bold")
    top_table.add_column("Composite", justify="right")
    top_table.add_column("Verdict")
    top_table.add_column("Sector")
    top_table.add_column("Source")
    for entry in ranked[:10]:
        top_table.add_row(
            entry.score.ticker,
            f"{entry.score.composite:.1f}",
            entry.score.verdict.value,
            entry.sector or "—",
            entry.source or "—",
        )
    console.print(top_table)


def _render_dip_candidates(
    console, dip_candidates: dict[str, DipCandidateRecord]
) -> None:
    if not dip_candidates:
        return
    dip_table = Table(title="Active dip candidates")
    dip_table.add_column("Ticker", style="bold")
    dip_table.add_column("Composite", justify="right")
    dip_table.add_column("Drawdown", justify="right")
    dip_table.add_column("Severity")
    dip_table.add_column("Reason")
    ranked = sorted(
        dip_candidates.values(),
        key=lambda r: r.dip.composite_score,
        reverse=True,
    )
    for record in ranked[:10]:
        dip_table.add_row(
            record.ticker,
            f"{record.dip.composite_score:.1f}",
            f"{record.dip.drawdown_pct:.1f}%",
            record.dip.severity.value,
            (record.dip.reason or "")[:60],
        )
    console.print(dip_table)


def _render_sector_coverage(console, sector_counter: Counter) -> None:
    if not sector_counter:
        return
    lines = [
        f"• {sector}: {count}" for sector, count in sector_counter.most_common()
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title="Sector coverage (cached universe)",
            title_align="left",
        )
    )


def _render_red_flags(console, red_flags: list[Signal]) -> None:
    if not red_flags:
        return
    rf_lines = [f"• {s.ticker}: {s.message}" for s in red_flags[:10]]
    console.print(
        Panel(
            "\n".join(rf_lines),
            title="Recent leadership red flags",
            title_align="left",
            border_style="red",
        )
    )


def _render_score_updates(console, score_updates: list[Signal]) -> None:
    if not score_updates:
        return
    recent = score_updates[-5:]
    su_lines = [
        f"• {s.ticker} → {s.message} ({s.timestamp[:19]})" for s in recent
    ]
    console.print(
        Panel(
            "\n".join(su_lines),
            title="Recent score updates",
            title_align="left",
            border_style="dim",
        )
    )


# ── Markdown mirror ─────────────────────────────────────────────────────────


def _render_markdown(
    scores: dict[str, ExtendedScore],
    dip_candidates: dict[str, DipCandidateRecord],
    red_flags: list[Signal],
    sector_counter: Counter,
) -> str:
    lines: list[str] = [
        f"# Market brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Cached universe: {len(scores)} tickers",
        f"- Dip candidates: {len(dip_candidates)}",
        f"- Leadership red flags: {len(red_flags)}",
        "",
    ]
    if scores:
        lines.append("## Top by composite")
        ranked = sorted(
            scores.values(), key=lambda e: e.score.composite, reverse=True
        )
        for entry in ranked[:10]:
            lines.append(
                f"- **{entry.score.ticker}** — composite "
                f"{entry.score.composite:.1f}, "
                f"{entry.score.verdict.value}, "
                f"{entry.sector or '—'}"
            )
        lines.append("")
    if dip_candidates:
        lines.append("## Active dip candidates")
        ranked = sorted(
            dip_candidates.values(),
            key=lambda r: r.dip.composite_score,
            reverse=True,
        )
        for record in ranked[:10]:
            lines.append(
                f"- **{record.ticker}** — composite "
                f"{record.dip.composite_score:.1f}, "
                f"drawdown {record.dip.drawdown_pct:.1f}%, "
                f"severity {record.dip.severity.value}"
            )
        lines.append("")
    if sector_counter:
        lines.append("## Sector coverage")
        for sector, count in sector_counter.most_common():
            lines.append(f"- {sector}: {count}")
        lines.append("")
    if red_flags:
        lines.append("## Leadership red flags")
        for s in red_flags[:10]:
            lines.append(f"- **{s.ticker}**: {s.message}")
    return "\n".join(lines)
