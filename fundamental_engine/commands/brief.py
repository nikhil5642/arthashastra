"""`brief` — market overview from cached state.

Reads cached scores, signals, and dip candidates and prints a structured
summary. No LLM narrative — Claude produces the commentary in the
`/market-brief` skill after reading this deterministic baseline.

Besides the ranking, the brief is where the early-signal layer pays off
across a whole watchlist: which names carry urgent concerns, which show
fresh strengths, and what changed (a verdict, a new concern) since each
was last looked at.

The brief is about the companies the user follows — the ones asked about
by name. A screen scores dozens more in passing; they stay in the cache
for the next screen and are only counted here.

Every piece of data this command touches is loaded as a typed object
(`ExtendedScore`, `DipCandidateRecord`, `Signal`) — there are no raw
JSON-dict lookups with fallback defaults anywhere in this file.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fundamental_engine.config import AnalystConfig
from fundamental_engine.dip_candidate import DipCandidateRecord
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.tracking import SIGNALS_FILENAME
from fundamental_engine.universe import load_dip_candidates, load_scores
from reports.formatting import make_console
from scorers.base import Gate
from scorers.early_signals import EarlySignal, Tone, of_tone, tally
from signals.signal_bus import Severity, Signal, SignalBus, SignalType

log = logging.getLogger(__name__)

# Cached rows older than this should be refreshed before acting on them.
STALE_AFTER = timedelta(days=2)
# How far back "what changed" looks, and how much of it is shown.
CHANGES_WINDOW = timedelta(days=14)
MAX_CHANGES_SHOWN = 15
MAX_SIGNAL_ROWS = 12

_CHANGE_TYPES: frozenset[SignalType] = frozenset(
    {SignalType.VERDICT_CHANGED, SignalType.EARLY_CONCERN, SignalType.EARLY_STRENGTH}
)
_CHANGE_MARKERS: dict[SignalType, str] = {
    SignalType.VERDICT_CHANGED: "◆",
    SignalType.EARLY_CONCERN: "▼",
    SignalType.EARLY_STRENGTH: "▲",
}


def run_brief_command() -> int:
    cfg = AnalystConfig.from_env()
    console = make_console()
    cache_dir = cfg.data_cache_dir

    cached = load_scores(cache_dir)
    scores = {ticker: extended for ticker, extended in cached.items() if extended.followed}
    in_passing = len(cached) - len(scores)
    dip_candidates = load_dip_candidates(cache_dir)
    signals = [
        signal
        for signal in SignalBus(path=cache_dir / SIGNALS_FILENAME).read_all()
        if not signal.ticker or signal.ticker in scores
    ]

    sector_counter: Counter[str] = Counter(
        extended.sector
        for extended in scores.values()
        if extended.sector and extended.sector != "Unknown"
    )

    vetoes = _integrity_veto_lines(scores)
    concerns = _signal_lines(scores, Tone.CONCERN)
    strengths = _signal_lines(scores, Tone.STRENGTH)
    changes = _recent_changes(signals)
    stale = _stale_tickers(scores)

    stale_note = (
        f"  ·  [yellow]Stale (> {STALE_AFTER.days}d): {len(stale)}[/yellow]"
        if stale
        else ""
    )
    console.print(
        Panel(
            f"[bold]Market brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}[/bold]\n"
            f"Following: {len(scores)} tickers  ·  "
            f"Dip candidates: {len(dip_candidates)}  ·  "
            f"Signals logged: {len(signals)}{stale_note}"
            + (
                f"\n[dim]{in_passing} more were scored by screens in passing and are not "
                f"shown — `analyse <TICKER>` follows one.[/dim]"
                if in_passing
                else ""
            ),
            title_align="left",
        )
    )

    _render_top_by_composite(console, scores)
    _render_dip_candidates(console, dip_candidates)
    _render_lines(console, vetoes, title="Integrity vetoes", border_style="red")
    _render_lines(
        console, concerns, title="Early concerns — urgent and material", border_style="red"
    )
    _render_lines(console, strengths, title="Early strengths", border_style="green")
    _render_lines(
        console,
        changes,
        title=f"What changed in the last {CHANGES_WINDOW.days} days",
        border_style="yellow",
    )
    _render_sector_coverage(console, sector_counter)
    _render_stale(console, stale)

    reports_dir = cache_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    fp = reports_dir / f"brief_{datetime.now().strftime('%Y-%m-%d')}.md"
    fp.write_text(
        _render_markdown(
            scores, dip_candidates, vetoes, concerns, strengths, changes, sector_counter, stale
        )
    )
    console.print(f"\n[dim]Report saved: {fp}[/dim]")

    if not scores:
        console.print(
            "\n[yellow]The universe is empty. Run `fundamental_engine "
            "refresh <TICKER>` on a few stocks to populate the cache.[/yellow]"
        )
    return 0


# ── Derived views ───────────────────────────────────────────────────────────


def _integrity_veto_lines(scores: dict[str, ExtendedScore]) -> list[str]:
    return [
        f"{ticker}: integrity veto in cached judgment — composite capped"
        for ticker, extended in sorted(scores.items())
        if Gate.INTEGRITY_VETO in extended.score.gates_applied
    ]


def _signal_lines(scores: dict[str, ExtendedScore], tone: Tone) -> list[str]:
    """One line per ticker with material signals of `tone`: the worst first.

    INFO-level signals stay in each ticker's own report; the brief is for
    what deserves attention across the whole universe.
    """
    rows: list[tuple[int, int, str]] = []
    for ticker, extended in scores.items():
        material = [
            s for s in of_tone(extended.early_signals, tone) if s.severity is not Severity.INFO
        ]
        if not material:
            continue
        urgent = sum(1 for s in material if s.severity is Severity.URGENT)
        rows.append((urgent, len(material), f"{ticker}: {_headline(material)}"))
    rows.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [line for _, _, line in rows[:MAX_SIGNAL_ROWS]]


def _headline(signals: list[EarlySignal]) -> str:
    """The most severe signal in full, and how many more there are."""
    first = signals[0]  # detect_early_signals sorts most severe first
    tag = "URGENT · " if first.severity is Severity.URGENT else ""
    more = f"  (+{len(signals) - 1} more)" if len(signals) > 1 else ""
    return f"{tag}{first.message}{more}"


def _recent_changes(signals: list[Signal]) -> list[str]:
    """Verdict moves and newly appeared early signals, newest first."""
    cutoff = (datetime.now() - CHANGES_WINDOW).isoformat()
    recent = [
        s for s in signals if s.signal_type in _CHANGE_TYPES and s.timestamp >= cutoff
    ]
    recent.sort(key=lambda s: s.timestamp, reverse=True)
    return [
        f"{_CHANGE_MARKERS[s.signal_type]} {s.timestamp[:10]}  {s.ticker}: {s.message}"
        for s in recent[:MAX_CHANGES_SHOWN]
    ]


def _stale_tickers(scores: dict[str, ExtendedScore]) -> list[str]:
    """Tickers whose cached row is older than STALE_AFTER (or undated)."""
    cutoff = datetime.now() - STALE_AFTER
    stale: list[str] = []
    for ticker, extended in sorted(scores.items()):
        try:
            updated = datetime.fromisoformat(extended.score.last_updated)
        except ValueError:
            stale.append(ticker)
            continue
        if updated.replace(tzinfo=None) < cutoff:
            stale.append(ticker)
    return stale


# ── Terminal panels ─────────────────────────────────────────────────────────


def _render_top_by_composite(
    console: Console, scores: dict[str, ExtendedScore]
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
    top_table.add_column("Early signals")
    for entry in ranked[:10]:
        top_table.add_row(
            entry.score.ticker,
            f"{entry.score.composite:.1f}",
            entry.score.verdict.value,
            entry.sector or "—",
            tally(entry.early_signals) or "—",
        )
    console.print(top_table)


def _render_dip_candidates(
    console: Console, dip_candidates: dict[str, DipCandidateRecord]
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


def _render_lines(
    console: Console, lines: list[str], *, title: str, border_style: str
) -> None:
    if not lines:
        return
    console.print(
        Panel(
            "\n".join(f"• {line}" for line in lines),
            title=title,
            title_align="left",
            border_style=border_style,
        )
    )


def _render_sector_coverage(console: Console, sector_counter: Counter[str]) -> None:
    _render_lines(
        console,
        [f"{sector}: {count}" for sector, count in sector_counter.most_common()],
        title="Sector coverage (cached universe)",
        border_style="dim",
    )


def _render_stale(console: Console, stale: list[str]) -> None:
    if not stale:
        return
    console.print(
        Panel(
            ", ".join(stale),
            title=f"Stale rows (> {STALE_AFTER.days} days) — run `refresh all` before acting",
            title_align="left",
            border_style="yellow",
        )
    )


# ── Markdown mirror ─────────────────────────────────────────────────────────


def _render_markdown(
    scores: dict[str, ExtendedScore],
    dip_candidates: dict[str, DipCandidateRecord],
    vetoes: list[str],
    concerns: list[str],
    strengths: list[str],
    changes: list[str],
    sector_counter: Counter[str],
    stale: list[str],
) -> str:
    lines: list[str] = [
        f"# Market brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Following: {len(scores)} tickers",
        f"- Dip candidates: {len(dip_candidates)}",
        f"- Integrity vetoes: {len(vetoes)}",
        f"- Tickers with material early concerns: {len(concerns)}",
        f"- Stale rows (> {STALE_AFTER.days}d): {len(stale)}",
        "",
    ]
    if scores:
        lines.append("## Top by composite")
        ranked = sorted(
            scores.values(), key=lambda e: e.score.composite, reverse=True
        )
        for entry in ranked[:10]:
            signals = tally(entry.early_signals)
            lines.append(
                f"- **{entry.score.ticker}** — composite "
                f"{entry.score.composite:.1f}, "
                f"{entry.score.verdict.value}, "
                f"{entry.sector or '—'}" + (f" — {signals}" if signals else "")
            )
        lines.append("")
    if dip_candidates:
        lines.append("## Active dip candidates")
        ranked_dips = sorted(
            dip_candidates.values(),
            key=lambda r: r.dip.composite_score,
            reverse=True,
        )
        for record in ranked_dips[:10]:
            lines.append(
                f"- **{record.ticker}** — composite "
                f"{record.dip.composite_score:.1f}, "
                f"drawdown {record.dip.drawdown_pct:.1f}%, "
                f"severity {record.dip.severity.value}"
            )
        lines.append("")
    for heading, section in (
        ("Integrity vetoes", vetoes),
        ("Early concerns — urgent and material", concerns),
        ("Early strengths", strengths),
        (f"What changed in the last {CHANGES_WINDOW.days} days", changes),
    ):
        if section:
            lines.append(f"## {heading}")
            lines.extend(f"- {line}" for line in section)
            lines.append("")
    if sector_counter:
        lines.append("## Sector coverage")
        for sector, count in sector_counter.most_common():
            lines.append(f"- {sector}: {count}")
        lines.append("")
    if stale:
        lines.append("## Stale rows")
        lines.append(", ".join(stale))
    return "\n".join(lines)
