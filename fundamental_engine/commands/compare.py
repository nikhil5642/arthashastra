"""`compare` — named companies side by side, ranked.

    compare NVDA AMD 2330.TW [--rank fundamentals|valuation|both] [--reuse-hours H] [--json]

The answer to "which of these is the better business?", and the last step
of any shortlist put together by hand: a theme Yahoo has no industry for
("AI infrastructure", "defence electronics"), or a screen plus the names
its one-industry-per-company label missed (Samsung Electronics makes
memory chips and is filed under Consumer Electronics).

Each ticker is scored like `refresh` — live, unless the cache holds a row
fetched within `--reuse-hours` — and the cards are ranked on the
deterministic pillars (`fundamental_engine.scorecard`). `render_shortlist`
is the table `screen --score` prints too.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from fundamental_engine.config import AnalystConfig
from fundamental_engine.engine import Engine
from fundamental_engine.scorecard import (
    DEFAULT_REUSE_HOURS,
    RankBy,
    Scorecard,
    Shortlist,
    score_tickers,
)
from reports.formatting import VERDICT_STYLES, format_money, make_console
from scorers.early_signals import tally

_RANK_LABELS: dict[RankBy, str] = {
    RankBy.FUNDAMENTALS: "fundamentals",
    RankBy.VALUATION: "valuation",
    RankBy.BOTH: "fundamentals + valuation",
    RankBy.RISK_ADJUSTED: "reward less risk",
}


def run_compare_command(
    tickers: Sequence[str],
    *,
    rank_by: RankBy = RankBy.RISK_ADJUSTED,
    reuse_hours: float = DEFAULT_REUSE_HOURS,
    as_json: bool = False,
) -> int:
    if not tickers:
        print("[compare] Name at least one ticker: compare NVDA AMD 2330.TW")
        return 2
    cfg = AnalystConfig.from_env()
    shortlist = asyncio.run(
        score_tickers(
            tickers, Engine(cfg), cfg.data_cache_dir, rank_by=rank_by, reuse_hours=reuse_hours
        )
    )
    if as_json:
        print(json.dumps(shortlist.to_dict(), indent=2))
    else:
        render_shortlist(make_console(), shortlist, base_currency=cfg.base_currency)
    return 0 if shortlist.cards else 3


def render_shortlist(console: Console, shortlist: Shortlist, *, base_currency: str) -> None:
    """The ranked scorecard table, and what could not be scored."""
    if shortlist.cards:
        console.print(_table(shortlist, base_currency))
        console.print(
            "[dim]Volatility is the yearly swing of the share price and Traded / day what "
            "changes hands on an average day: risk is measured per company, not assumed from "
            "its size. Risk-adj. is Both less a discount for price swings, loss years and a "
            "short record; it orders this list and changes no verdict.[/dim]"
        )
        console.print(
            "[dim]Ranked on the deterministic pillars. Leadership, prospects and news are "
            "Claude's: a name marked 'not judged' carries a neutral 50 there, so its composite "
            "is half placeholder — run /analyse-stock before acting on it.[/dim]"
        )
        if shortlist.reused:
            console.print(
                f"[dim]{shortlist.reused} of {len(shortlist.cards)} taken from the cache "
                f"(fetched recently); --reuse-hours 0 fetches everything live.[/dim]"
            )
    for result in shortlist.not_scored:
        detail = f" — {result.detail}" if result.detail else ""
        console.print(f"[yellow]Not scored: {result.ticker} ({result.status}){detail}[/yellow]")
    if not shortlist.cards and not shortlist.not_scored:
        console.print("[yellow]Nothing to compare.[/yellow]")


def _table(shortlist: Shortlist, base_currency: str) -> Table:
    table = Table(title=f"Scored — ranked by {_RANK_LABELS[shortlist.ranked_by]}")
    for column, justify in (
        ("#", "right"), ("Ticker", "left"), ("Company", "left"), ("Country", "left"),
        ("Market cap", "right"), ("Volatility", "right"), ("Traded / day", "right"),
        ("Fundamentals", "right"), ("Valuation", "right"),
        ("Both", "right"), ("Risk-adj.", "right"), ("Composite", "left"), ("Early signals", "left"),
    ):
        table.add_column(column, justify=justify)
    for rank, card in enumerate(shortlist.cards, start=1):
        size = f" [dim]{card.size_band}[/dim]" if card.size_band else ""
        table.add_row(
            str(rank),
            f"[bold]{card.ticker}[/bold]",
            card.company_name[:30] or "—",
            card.country or "—",
            (format_money(card.market_cap_base, base_currency) if card.market_cap_base else "—") + size,
            f"{card.risk.volatility_1y_pct:.0f}%" if card.risk.volatility_1y_pct is not None else "—",
            format_money(card.traded_per_day_base, base_currency) if card.traded_per_day_base else "—",
            f"{card.fundamentals:.1f}",
            f"{card.valuation:.1f}" if card.valuation is not None else "[dim]unscored[/dim]",
            f"{card.both:.1f}",
            f"{card.risk_adjusted:.1f}",
            _composite_cell(card),
            tally(card.early_signals) or "—",
        )
    return table


def _composite_cell(card: Scorecard) -> str:
    style = VERDICT_STYLES.get(card.verdict, "white")
    cell = f"{card.composite:.1f} [{style}]{card.verdict}[/{style}]"
    if card.integrity_veto:
        cell += " [red]· integrity veto[/red]"
    if not card.judged:
        cell += " [dim]· not judged[/dim]"
    return cell
