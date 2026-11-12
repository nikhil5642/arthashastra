"""`pick --amount <BUDGET>` — budget-constrained buy-the-dip candidates.

Reads cached scores + the real price history SQLite DB (populated by
`analyse` / `refresh` / the engine). For each ticker, builds a real
PriceSnapshot from the stored daily closes (SMA / RSI from actual yfinance
data — nothing synthesised), runs the dip gate, then ranks, diversifies,
and allocates within the budget.

The budget is in the base currency (`BASE_CURRENCY`, default INR). Each
listing's price is converted with the FX rate cached at refresh time, so
a ₹ budget can size positions in MSFT, 0700.HK, or RELIANCE.NS alike.
Picks are diversified across sectors AND countries. `--tickers` narrows the
universe to a chosen set — typically the names `screen --score` just
cached from a global screen.

No position is sized above a tenth of what the stock trades on an average
day (`MAX_SHARE_OF_DAILY_TRADING_PCT`): a holding you cannot sell in a day
without moving the price is a risk the scores don't see. That limit is
what a small, thinly traded company costs here — it is measured from the
tape, not assumed from the company's size.

Blocked tickers: anything whose cached score carries the integrity gate.
Early signals never block a pick — a dip usually comes with concerns —
but every pick shows its tally, and urgent concerns are spelled out under
the table so they are read before money moves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fundamental_engine.budget_parser import parse_budget
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.dip_candidate import DipCandidateRecord
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.scorecard import in_base_currency
from fundamental_engine.ticker import canonical
from fundamental_engine.universe import load_scores
from reports.formatting import (
    VERDICT_STYLES,
    format_amount,
    format_price,
    make_console,
)
from scorers.base import Gate, Verdict
from scorers.early_signals import EarlySignal, Tone, of_tone, tally
from screener.dip_detector import (
    DEFAULT_MIN_COMPOSITE,
    DipSignal,
    allowed_verdicts_from_min,
    check_dip,
    dip_rank_key,
)
from screener.price_history import PriceHistoryStore, PriceSnapshot
from signals.signal_bus import Severity, Signal, SignalBus, SignalType

log = logging.getLogger(__name__)

MAX_PICKS = 8
MAX_PER_SECTOR = 2
MAX_PER_COUNTRY = 4
MIN_ALLOCATION_PCT = 5.0
MAX_ALLOCATION_PCT = 25.0
MAX_SHARE_OF_DAILY_TRADING_PCT = 10.0  # of an average day's traded value, per position
PRICE_DB_FILENAME = "prices.db"


@dataclass(slots=True)
class Candidate:
    ticker: str
    name: str
    sector: str
    country: str
    currency: str
    current_price: float  # in the listing's trading currency
    price_in_base: float | None  # None → no FX quote, can't size a position
    composite: float
    verdict: Verdict
    dip: DipSignal
    early_signals: list[EarlySignal] = field(default_factory=list)
    traded_per_day_base: float | None = None  # an average day's trading; None when unknown
    volatility_1y_pct: float | None = None

    def liquidity_ceiling(self) -> float | None:
        """The most this position may be, in the base currency; None when trading is unknown."""
        if self.traded_per_day_base is None:
            return None
        return self.traded_per_day_base * MAX_SHARE_OF_DAILY_TRADING_PCT / 100


def _blocked_tickers(scores: dict[str, ExtendedScore]) -> set[str]:
    """Tickers whose cached score carries the integrity gate."""
    return {
        ticker
        for ticker, extended in scores.items()
        if Gate.INTEGRITY_VETO in extended.score.gates_applied
    }


def _gather_candidates(
    scores: dict[str, ExtendedScore],
    cache_dir: Path,
    *,
    min_composite: float,
    allowed_verdicts: frozenset[Verdict],
    blocked: set[str],
) -> tuple[list[Candidate], int]:
    """Return (candidates, tickers_missing_history)."""
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
                yoy_pat_growth=extended.latest_yoy_pat_growth,
                yoy_revenue_growth=extended.latest_yoy_revenue_growth,
                min_composite=min_composite,
                allowed_verdicts=allowed_verdicts,
            )
            if not check.qualifies or check.dip is None:
                continue

            price = float(price_snapshot.current_price)
            candidates.append(
                Candidate(
                    ticker=ticker,
                    name=extended.company_name or ticker,
                    sector=extended.sector or "Unknown",
                    country=extended.country or "Unknown",
                    currency=extended.currency,
                    current_price=price,
                    price_in_base=_price_in_base(extended, price),
                    composite=extended.score.composite,
                    verdict=extended.score.verdict,
                    dip=check.dip,
                    early_signals=extended.early_signals,
                    traded_per_day_base=in_base_currency(
                        extended, extended.risk.avg_daily_traded_value
                    ),
                    volatility_1y_pct=extended.risk.volatility_1y_pct,
                )
            )
    return candidates, missing_history


def _price_in_base(extended: ExtendedScore, price: float) -> float | None:
    """Convert a trading-currency price with the FX rate cached at refresh.

    Rows cached before currencies were tracked carry no currency; those
    were all base-currency (INR) listings, so their price is used as-is.
    """
    if not extended.currency:
        return price
    if extended.fx_rate_to_base is None:
        return None
    return price * extended.fx_rate_to_base


def _diversify(ranked: list[Candidate]) -> list[Candidate]:
    """Best-ranked first, at most MAX_PER_SECTOR per sector and MAX_PER_COUNTRY per country."""
    out: list[Candidate] = []
    per_sector: dict[str, int] = {}
    per_country: dict[str, int] = {}
    for c in ranked:
        if per_sector.get(c.sector, 0) >= MAX_PER_SECTOR:
            continue
        if per_country.get(c.country, 0) >= MAX_PER_COUNTRY:
            continue
        out.append(c)
        per_sector[c.sector] = per_sector.get(c.sector, 0) + 1
        per_country[c.country] = per_country.get(c.country, 0) + 1
        if len(out) >= MAX_PICKS:
            break
    return out


def _allocate(
    picks: list[Candidate], budget: float
) -> dict[str, tuple[int, float]]:
    """Equal-weight whole-share allocation in the base currency, capped per position.

    A position is also held to a tenth of an average day's trading in the
    stock, when that is known. A pick is dropped only when it has no
    FX-converted price, not even one share fits its slice, or the position
    would be too small to matter (< MIN_ALLOCATION_PCT of budget). Share counts are indicative — check
    the exchange's lot size (e.g. 100-share units in Japan) before ordering.
    """
    if not picks:
        return {}
    per_position = min(budget / len(picks), budget * MAX_ALLOCATION_PCT / 100)
    allocations: dict[str, tuple[int, float]] = {}
    for c in picks:
        if c.price_in_base is None or c.price_in_base <= 0:
            continue
        ceiling = c.liquidity_ceiling()
        slice_ = per_position if ceiling is None else min(per_position, ceiling)
        shares = int(slice_ // c.price_in_base)
        if shares < 1:
            continue
        amount = round(shares * c.price_in_base, 2)
        if amount < budget * MIN_ALLOCATION_PCT / 100:
            continue
        allocations[c.ticker] = (shares, amount)
    return allocations


def _render(
    picks: list[Candidate],
    allocations: dict[str, tuple[int, float]],
    budget: float,
    *,
    base_currency: str,
    min_composite: float,
    min_verdict: str,
    console: Console,
    universe_size: int,
    missing_history: int,
    chosen: bool = False,
    not_cached: tuple[str, ...] = (),
) -> None:
    console.print(
        Panel(
            f"Budget: {format_amount(budget, base_currency)}  ·  "
            f"{'Chosen tickers in cache' if chosen else 'Universe in cache'}: {universe_size}  ·  "
            f"Candidates found: {len(picks)}  ·  "
            f"Gate: composite ≥ {min_composite} and verdict ≥ {min_verdict}",
            title="Buy-the-dip picks",
            title_align="left",
        )
    )

    if not_cached:
        console.print(
            f"[yellow]Not in cache: {', '.join(not_cached)} — run `fundamental_engine "
            f"screen … --score N` or `refresh <TICKER>` first.[/yellow]"
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
    table.add_column("Country")
    table.add_column("Price", justify="right")
    table.add_column("Composite", justify="right")
    table.add_column("Verdict")
    table.add_column("Drawdown", justify="right")
    table.add_column("Severity")
    table.add_column("Early signals")
    table.add_column("Volatility", justify="right")
    table.add_column("Traded / day", justify="right")
    table.add_column("Shares", justify="right")
    table.add_column("Allocation", justify="right")

    total_alloc = 0.0
    for c in picks:
        alloc = allocations.get(c.ticker)
        if alloc is None:
            shares_str = "-"
            alloc_str = "(no FX rate)" if c.price_in_base is None else _unallocated_reason(c, budget)
        else:
            shares, amount = alloc
            shares_str = str(shares)
            alloc_str = format_amount(amount, base_currency)
            total_alloc += amount
        verdict_style = VERDICT_STYLES.get(c.verdict, "white")
        table.add_row(
            c.ticker,
            c.sector,
            c.country,
            format_price(c.current_price, c.currency or base_currency),
            f"{c.composite:.1f}",
            f"[{verdict_style}]{c.verdict}[/{verdict_style}]",
            f"{c.dip.drawdown_pct:.1f}%",
            c.dip.severity,
            tally(c.early_signals) or "—",
            f"{c.volatility_1y_pct:.0f}%" if c.volatility_1y_pct is not None else "—",
            format_amount(c.traded_per_day_base, base_currency) if c.traded_per_day_base else "—",
            shares_str,
            alloc_str,
        )
    console.print(table)
    _render_urgent_concerns(picks, console)

    sector_breakdown: dict[str, float] = {}
    for c in picks:
        if c.ticker in allocations:
            sector_breakdown[c.sector] = (
                sector_breakdown.get(c.sector, 0.0) + allocations[c.ticker][1]
            )

    summary_lines = [
        f"Allocated: {format_amount(total_alloc, base_currency)}  ·  "
        f"Remaining: {format_amount(budget - total_alloc, base_currency)}",
        "Share counts are indicative — check each exchange's lot size before ordering.",
    ]
    if sector_breakdown:
        summary_lines.append("Sector breakdown:")
        for sector, amount in sorted(
            sector_breakdown.items(), key=lambda kv: -kv[1]
        ):
            pct = 100 * amount / budget
            summary_lines.append(
                f"  • {sector}: {format_amount(amount, base_currency)} ({pct:.1f}%)"
            )
    console.print(
        Panel(
            "\n".join(summary_lines),
            title="Portfolio rollup",
            title_align="left",
        )
    )


def _unallocated_reason(candidate: Candidate, budget: float) -> str:
    """Why a pick got no shares: too thinly traded for a position worth having, or too small."""
    ceiling = candidate.liquidity_ceiling()
    if ceiling is not None and ceiling < budget * MIN_ALLOCATION_PCT / 100:
        return "(too thinly traded)"
    return "(below min)"


def _render_urgent_concerns(picks: list[Candidate], console: Console) -> None:
    """Spell out every urgent concern among the picks — read before buying."""
    lines = [
        f"• [bold]{c.ticker}[/bold]: {signal.message}"
        for c in picks
        for signal in of_tone(c.early_signals, Tone.CONCERN)
        if signal.severity is Severity.URGENT
    ]
    if lines:
        console.print(
            Panel(
                "\n".join(lines),
                title="Urgent concerns among the picks — read before buying",
                title_align="left",
                border_style="red",
            )
        )


def run_pick_command(
    amount_raw: str,
    *,
    min_composite: float = DEFAULT_MIN_COMPOSITE,
    min_verdict: str = "BUY",
    tickers: list[str] | None = None,
) -> int:
    cfg = AnalystConfig.from_env()
    budget = parse_budget(amount_raw)
    if budget is None or budget <= 0:
        print(
            f"[pick] Invalid budget: {amount_raw!r}. Amounts are in "
            f"{cfg.base_currency}; try '100000', '50k', '2.5m', or '1L'."
        )
        return 4

    console = make_console()
    allowed_verdicts = allowed_verdicts_from_min(min_verdict)

    bus = SignalBus(path=cfg.data_cache_dir / "signals.json")
    scores = load_scores(cfg.data_cache_dir)
    not_cached: tuple[str, ...] = ()
    if tickers is not None:
        chosen = [canonical(t) for t in tickers]
        not_cached = tuple(t for t in chosen if t not in scores)
        scores = {t: scores[t] for t in chosen if t in scores}

    candidates, missing_history = _gather_candidates(
        scores,
        cfg.data_cache_dir,
        min_composite=min_composite,
        allowed_verdicts=allowed_verdicts,
        blocked=_blocked_tickers(scores),
    )

    ranked = sorted(candidates, key=lambda c: dip_rank_key(c.dip), reverse=True)
    picks = _diversify(ranked)
    allocations = _allocate(picks, budget)

    _render(
        picks,
        allocations,
        budget,
        base_currency=cfg.base_currency,
        min_composite=min_composite,
        min_verdict=min_verdict,
        console=console,
        universe_size=len(scores),
        missing_history=missing_history,
        chosen=tickers is not None,
        not_cached=not_cached,
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
