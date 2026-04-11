"""`brief` — market overview from cached state.

Reads cached scores, signals, and dip candidates and prints a structured
summary. No LLM narrative — Claude produces the commentary in the
`/market-brief` skill after reading this deterministic baseline.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.universe import load_universe_with_metadata
from reports.formatting import make_console
from signals.signal_bus import Signal, SignalBus, SignalType

log = logging.getLogger(__name__)


def run_brief_command() -> int:
    cfg = AnalystConfig.from_env()
    console = make_console()
    cache_dir = cfg.data_cache_dir

    universe = load_universe_with_metadata(cache_dir)
    dip_candidates = JsonStore(cache_dir / "dip_candidates.json").read()
    bus = SignalBus(path=cache_dir / "signals.json")
    signals = bus.read_all()

    sectors = [entry.get("sector") or "Unknown" for entry in universe]
    sector_counter = Counter(s for s in sectors if s and s != "Unknown")

    red_flags = [s for s in signals if s.signal_type == SignalType.LEADERSHIP_RED_FLAG]
    score_updates = [s for s in signals if s.signal_type == SignalType.SCORE_UPDATED]

    console.print(
        Panel(
            f"[bold]Market brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}[/bold]\n"
            f"Cached universe: {len(universe)} tickers  ·  "
            f"Dip candidates: {len(dip_candidates)}  ·  "
            f"Signals logged: {len(signals)}",
            title_align="left",
        )
    )

    if universe:
        ranked = sorted(
            universe, key=lambda e: e.get("composite") or 0, reverse=True
        )
        top_table = Table(title="Top by composite")
        top_table.add_column("Ticker", style="bold")
        top_table.add_column("Composite", justify="right")
        top_table.add_column("Verdict")
        top_table.add_column("Sector")
        top_table.add_column("Source")
        for entry in ranked[:10]:
            top_table.add_row(
                entry.get("ticker", ""),
                f"{entry.get('composite') or 0:.1f}",
                entry.get("verdict") or "—",
                entry.get("sector") or "—",
                entry.get("source") or "—",
            )
        console.print(top_table)

    if dip_candidates:
        dip_table = Table(title="Active dip candidates")
        dip_table.add_column("Ticker", style="bold")
        dip_table.add_column("Composite", justify="right")
        dip_table.add_column("Drawdown", justify="right")
        dip_table.add_column("Severity")
        dip_table.add_column("Reason")
        for ticker, info in sorted(
            dip_candidates.items(),
            key=lambda kv: kv[1].get("composite_score") or 0,
            reverse=True,
        )[:10]:
            dip_table.add_row(
                ticker,
                f"{info.get('composite_score') or 0:.1f}",
                f"{info.get('drawdown_pct') or 0:.1f}%",
                info.get("severity") or "—",
                (info.get("reason") or "")[:60],
            )
        console.print(dip_table)

    if sector_counter:
        lines = [f"• {sector}: {count}" for sector, count in sector_counter.most_common()]
        console.print(
            Panel(
                "\n".join(lines),
                title="Sector coverage (cached universe)",
                title_align="left",
            )
        )

    if red_flags:
        rf_lines = [f"• {s.ticker}: {s.message}" for s in red_flags[:10]]
        console.print(
            Panel(
                "\n".join(rf_lines),
                title="Recent leadership red flags",
                title_align="left",
                border_style="red",
            )
        )

    if score_updates:
        recent = score_updates[-5:]
        su_lines = [
            f"• {s.ticker} → {s.message} ({s.timestamp[:19]})"
            for s in recent
        ]
        console.print(
            Panel(
                "\n".join(su_lines),
                title="Recent score updates",
                title_align="left",
                border_style="dim",
            )
        )

    reports_dir = cache_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    fp = reports_dir / f"brief_{datetime.now().strftime('%Y-%m-%d')}.md"
    fp.write_text(
        _markdown(universe, dip_candidates, red_flags, sector_counter, score_updates)
    )
    console.print(f"\n[dim]Report saved: {fp}[/dim]")

    if not universe:
        console.print(
            "\n[yellow]The universe is empty. Run `fundamental_engine "
            "refresh <TICKER>` on a few stocks to populate the cache.[/yellow]"
        )
    return 0


def _markdown(
    universe: list[dict],
    dip_candidates: dict,
    red_flags: list[Signal],
    sector_counter: Counter,
    score_updates: list[Signal],
) -> str:
    lines: list[str] = [
        f"# Market brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Cached universe: {len(universe)} tickers",
        f"- Dip candidates: {len(dip_candidates)}",
        f"- Leadership red flags: {len(red_flags)}",
        "",
    ]
    if universe:
        lines.append("## Top by composite")
        ranked = sorted(
            universe, key=lambda e: e.get("composite") or 0, reverse=True
        )
        for entry in ranked[:10]:
            lines.append(
                f"- **{entry.get('ticker')}** — composite "
                f"{entry.get('composite') or 0:.1f}, "
                f"{entry.get('verdict') or '—'}, "
                f"{entry.get('sector') or '—'}"
            )
        lines.append("")
    if dip_candidates:
        lines.append("## Active dip candidates")
        for ticker, info in sorted(
            dip_candidates.items(),
            key=lambda kv: kv[1].get("composite_score") or 0,
            reverse=True,
        )[:10]:
            lines.append(
                f"- **{ticker}** — composite "
                f"{info.get('composite_score') or 0:.1f}, "
                f"drawdown {info.get('drawdown_pct') or 0:.1f}%, "
                f"severity {info.get('severity') or '—'}"
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
