"""`pick --amount <INR>` — budget-constrained buy-the-dip candidates.

Reads cached scores + the real price history SQLite DB (populated by
`analyse` / `refresh` / the engine). For each ticker, builds a real
PriceSnapshot from the stored daily closes (SMA / RSI from actual yfinance
data — nothing synthesised), runs the dip gate, then ranks, diversifies,
and allocates within the budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fundamental_engine.budget_parser import parse_budget
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.dip_candidate import DipCandidateRecord
from fundamental_engine.universe import load_scores
from reports.formatting import VERDICT_STYLES, make_console
from scorers.base import Verdict
from screener.dip_detector import (
    DEFAULT_MIN_COMPOSITE,
    DipSignal,
    allowed_verdicts_from_min,
    check_dip,
    dip_rank_key,
)
from screener.price_history import PriceHistoryStore, PriceSnapshot
from signals.signal_bus import Signal, SignalBus, SignalType

log = logging.getLogger(__name__)

MAX_PICKS = 8
MAX_PER_SECTOR = 2
MIN_SHARES_AFFORDABLE = 10
MIN_ALLOCATION_PCT = 5.0
MAX_ALLOCATION_PCT = 25.0
PRICE_DB_FILENAME = "prices.db"


@dataclass(slots=True)
class Candidate:
    ticker: str
    name: str
    sector: str
    current_price: float
    composite: float
    verdict: Verdict
    dip: DipSignal


def _gather_candidates(
    cache_dir,
    *,
    min_composite: float,
    allowed_verdicts: frozenset[Verdict],
    blocked: set[str],
) -> tuple[list[Candidate], int]:
    """Return (candidates, tickers_missing_history)."""
    scores = load_scores(cache_dir)
    if not scores:
        return [], 0

    missing_history = 0
    candidates: list[Candidate] = []
    with PriceHistoryStore(cache_dir / PRICE_DB_FILENAME) as store:
        for ticker, extended in scores.items():
            price_snapshot: PriceSnapshot | None = store.snapshot(ticker)
            if price_snapshot is None:
                # The engine/refresh must persist daily closes before pick
                # can run the dip detector on this ticker.
                missing_history += 1
                continue

            check = check_dip(
                score=extended.score,
                prices=price_snapshot,
                blocked_tickers=blocked,
                min_composite=min_composite,
                allowed_verdicts=allowed_verdicts,
            )
            if not check.qualifies or check.dip is None:
                continue

            candidates.append(
                Candidate(
                    ticker=ticker,
                    name=extended.company_name or ticker,
                    sector=extended.sector or "Unknown",
                    current_price=float(price_snapshot.current_price),
                    composite=extended.score.composite,
                    verdict=extended.score.verdict,
                    dip=check.dip,
                )
            )
    return candidates, missing_history


def _diversify(
    ranked: list[Candidate], max_picks: int, max_per_sector: int
) -> list[Candidate]:
    out: list[Candidate] = []
    sector_count: dict[str, int] = {}
    for c in ranked:
        if sector_count.get(c.sector, 0) >= max_per_sector:
            continue
        out.append(c)
        sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
        if len(out) >= max_picks:
            break
    return out


def _allocate(
    picks: list[Candidate], budget: float
) -> dict[str, tuple[int, float]]:
    if not picks:
        return {}
    target_per = budget / len(picks)
    max_per = budget * MAX_ALLOCATION_PCT / 100
    allocations: dict[str, tuple[int, float]] = {}
    for c in picks:
        target = min(target_per, max_per)
        shares = int(target // c.current_price) if c.current_price > 0 else 0
        if shares < MIN_SHARES_AFFORDABLE:
            continue
        rupees = round(shares * c.current_price, 2)
        if rupees < budget * MIN_ALLOCATION_PCT / 100:
            continue
        allocations[c.ticker] = (shares, rupees)
    return allocations


def _render(
    picks: list[Candidate],
    allocations: dict[str, tuple[int, float]],
    budget: float,
    *,
    min_composite: float,
    min_verdict: str,
    console: Console,
    universe_size: int,
    missing_history: int,
) -> None:
    console.print(
        Panel(
            f"Budget: ₹{budget:,.0f}  ·  "
            f"Universe in cache: {universe_size}  ·  "
            f"Candidates found: {len(picks)}  ·  "
            f"Gate: composite ≥ {min_composite} and verdict ≥ {min_verdict}",
            title="Buy-the-dip picks",
            title_align="left",
        )
    )

    if universe_size == 0:
        console.print(
            "[yellow]No tickers in cache. Run `fundamental_engine refresh "
            "<TICKER>` on a few stocks first, then try again.[/yellow]"
        )
        return

    if missing_history:
        console.print(
            f"[yellow]{missing_history} ticker(s) skipped — no cached price "
            f"history yet. Run `fundamental_engine refresh <TICKER>` to "
            f"populate the price store.[/yellow]"
        )

    if not picks:
        console.print(
            "[yellow]No candidates matched the quality + dip gate. "
            "Lower --min-composite / --min-verdict to widen, or wait for "
            "more tickers to warm up in the cache.[/yellow]"
        )
        return

    table = Table(title="Ranked picks", title_style="bold")
    table.add_column("Ticker", style="bold")
    table.add_column("Sector")
    table.add_column("Price", justify="right")
    table.add_column("Composite", justify="right")
    table.add_column("Verdict")
    table.add_column("Drawdown", justify="right")
    table.add_column("Severity")
    table.add_column("Shares", justify="right")
    table.add_column("Allocation", justify="right")

    total_alloc = 0.0
    for c in picks:
        alloc = allocations.get(c.ticker)
        if alloc is None:
            shares_str = "-"
            alloc_str = "(below min)"
        else:
            shares, rupees = alloc
            shares_str = str(shares)
            alloc_str = f"₹{rupees:,.0f}"
            total_alloc += rupees
        verdict_style = VERDICT_STYLES.get(c.verdict, "white")
        table.add_row(
            c.ticker,
            c.sector,
            f"₹{c.current_price:,.2f}",
            f"{c.composite:.1f}",
            f"[{verdict_style}]{c.verdict}[/{verdict_style}]",
            f"{c.dip.drawdown_pct:.1f}%",
            c.dip.severity,
            shares_str,
            alloc_str,
        )
    console.print(table)

    sector_breakdown: dict[str, float] = {}
    for c in picks:
        if c.ticker in allocations:
            sector_breakdown[c.sector] = (
                sector_breakdown.get(c.sector, 0.0) + allocations[c.ticker][1]
            )

    summary_lines = [
        f"Allocated: ₹{total_alloc:,.0f}  ·  "
        f"Remaining: ₹{budget - total_alloc:,.0f}",
    ]
    if sector_breakdown:
        summary_lines.append("Sector breakdown:")
        for sector, rupees in sorted(
            sector_breakdown.items(), key=lambda kv: -kv[1]
        ):
            pct = 100 * rupees / budget
            summary_lines.append(f"  • {sector}: ₹{rupees:,.0f} ({pct:.1f}%)")
    console.print(
        Panel(
            "\n".join(summary_lines),
            title="Portfolio rollup",
            title_align="left",
        )
    )


def run_pick_command(
    amount_raw: str,
    *,
    min_composite: float = DEFAULT_MIN_COMPOSITE,
    min_verdict: str = "BUY",
) -> int:
    budget = parse_budget(amount_raw)
    if budget is None or budget <= 0:
        print(f"[pick] Invalid budget: {amount_raw!r}. Try '100000' or '1L'.")
        return 4

    cfg = AnalystConfig.from_env()
    console = make_console()
    allowed_verdicts = allowed_verdicts_from_min(min_verdict)

    bus = SignalBus(path=cfg.data_cache_dir / "signals.json")
    blocked: set[str] = {
        s.ticker
        for s in bus.read_all()
        if s.signal_type == SignalType.LEADERSHIP_RED_FLAG
    }

    universe_size = len(load_scores(cfg.data_cache_dir))

    candidates, missing_history = _gather_candidates(
        cfg.data_cache_dir,
        min_composite=min_composite,
        allowed_verdicts=allowed_verdicts,
        blocked=blocked,
    )

    ranked = sorted(candidates, key=lambda c: dip_rank_key(c.dip), reverse=True)
    picks = _diversify(ranked, MAX_PICKS, MAX_PER_SECTOR)
    allocations = _allocate(picks, budget)

    _render(
        picks,
        allocations,
        budget,
        min_composite=min_composite,
        min_verdict=min_verdict,
        console=console,
        universe_size=universe_size,
        missing_history=missing_history,
    )

    # Persist dip candidates as typed DipCandidateRecord rows. `brief`
    # reads them back through `load_dip_candidates` which returns the
    # same typed objects — no raw-dict access on either side.
    JsonStore(cfg.data_cache_dir / "dip_candidates.json").write(
        {
            c.ticker: DipCandidateRecord.from_dip(
                ticker=c.ticker,
                name=c.name,
                sector=c.sector,
                dip=c.dip,
            ).to_dict()
            for c in picks
        }
    )

    for c in picks:
        bus.emit(
            Signal(
                signal_type=SignalType.DIP_DETECTED,
                severity=c.dip.severity,
                ticker=c.ticker,
                message=c.dip.reason,
                data={
                    "composite": c.composite,
                    "drawdown_pct": c.dip.drawdown_pct,
                },
            )
        )
    return 0
