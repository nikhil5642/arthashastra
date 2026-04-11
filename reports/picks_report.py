"""`pick --amount <INR>` — budget-constrained buy-the-dip candidates.

Reads cached scores (populated by `analyse` / `refresh` / the engine). For
each ticker, reconstructs a deterministic dip check from the cached price
anchors + composite score, then ranks, diversifies, and allocates within
the budget. No live fetches here — everything comes from the cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_sources.fundamentals_source import BalanceSheetSnapshot
from fundamental_engine.budget_parser import parse_budget
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.universe import load_universe_with_metadata
from reports.formatting import VERDICT_STYLES, make_console
from scorers.base import StockScore
from screener.dip_detector import (
    DEFAULT_MIN_COMPOSITE,
    DipSignal,
    allowed_verdicts_from_min,
    check_dip,
    dip_rank_key,
)
from screener.price_history import (
    PriceHistory,
    PriceSnapshot,
    synthesize_from_anchors,
)
from signals.signal_bus import Severity, Signal, SignalBus, SignalType

log = logging.getLogger(__name__)

MAX_PICKS = 8
MAX_PER_SECTOR = 2
MIN_SHARES_AFFORDABLE = 10
MIN_ALLOCATION_PCT = 5.0
MAX_ALLOCATION_PCT = 25.0


@dataclass(slots=True)
class Candidate:
    ticker: str
    name: str
    sector: str
    current_price: float
    composite: float
    verdict: str
    dip: DipSignal


def _snap_from_cache(ticker: str, data: dict) -> BalanceSheetSnapshot | None:
    if not all(
        data.get(k) is not None for k in ("current_price", "high_52w", "low_52w")
    ):
        return None
    return BalanceSheetSnapshot(
        ticker=ticker,
        as_of_date=data.get("last_updated", ""),
        company_name=data.get("company_name", ""),
        sector=data.get("sector", ""),
        industry=data.get("industry", ""),
        source=data.get("source", "cache"),
        current_price=float(data["current_price"]),
        high_52w=float(data["high_52w"]),
        low_52w=float(data["low_52w"]),
        market_cap_cr=float(data["market_cap_cr"]) if data.get("market_cap_cr") else None,
    )


def _stock_score_from_cache(ticker: str, data: dict) -> StockScore | None:
    if "composite" not in data or "verdict" not in data:
        return None
    try:
        return StockScore(
            ticker=ticker,
            leadership_score=float(data.get("leadership_score", 50)),
            fundamentals_score=float(data.get("fundamentals_score", 50)),
            prospects_score=float(data.get("prospects_score", 50)),
            news_score=float(data.get("news_score", 50)),
            composite=float(data["composite"]),
            verdict=data["verdict"],
            confidence=float(data.get("confidence", 0.5)),
            data_completeness_pct=float(data.get("data_completeness_pct", 50)),
            evidence=data.get("evidence", {}),
            last_updated=data.get("last_updated", ""),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _build_price_snapshot(
    snap: BalanceSheetSnapshot, history: PriceHistory
) -> PriceSnapshot | None:
    if (
        snap.current_price is None
        or snap.high_52w is None
        or snap.low_52w is None
    ):
        return None
    if history.latest(snap.ticker) is None:
        rows = synthesize_from_anchors(
            ticker=snap.ticker,
            current_price=snap.current_price,
            high_52w=snap.high_52w,
            low_52w=snap.low_52w,
        )
        history.load_rows(snap.ticker, rows)
    return history.snapshot(snap.ticker)


def _gather_candidates(
    cache_dir,
    *,
    min_composite: float,
    allowed_verdicts: frozenset[str],
    blocked: set[str],
) -> list[Candidate]:
    entries = load_universe_with_metadata(cache_dir)
    if not entries:
        return []

    history = PriceHistory(":memory:")
    candidates: list[Candidate] = []
    try:
        for entry in entries:
            ticker = entry["ticker"]
            score = _stock_score_from_cache(ticker, entry)
            snap = _snap_from_cache(ticker, entry)
            if score is None or snap is None:
                continue

            prices = _build_price_snapshot(snap, history)
            if prices is None:
                continue

            check = check_dip(
                score=score,
                prices=prices,
                blocked_tickers=blocked,
                min_composite=min_composite,
                allowed_verdicts=allowed_verdicts,
            )
            if not check.qualifies or check.dip is None:
                continue

            candidates.append(
                Candidate(
                    ticker=ticker,
                    name=snap.company_name or ticker,
                    sector=snap.sector or "Unknown",
                    current_price=snap.current_price or 0.0,
                    composite=score.composite,
                    verdict=score.verdict,
                    dip=check.dip,
                )
            )
        return candidates
    finally:
        history.close()


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

    universe_size = len(load_universe_with_metadata(cfg.data_cache_dir))

    candidates = _gather_candidates(
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
    )

    JsonStore(cfg.data_cache_dir / "dip_candidates.json").write(
        {
            c.ticker: {
                **c.dip.to_dict(),
                "sector": c.sector,
                "name": c.name,
            }
            for c in picks
        }
    )

    for c in picks:
        bus.emit(
            Signal(
                signal_type=SignalType.DIP_DETECTED,
                severity=_sev(c.dip.severity),
                ticker=c.ticker,
                message=c.dip.reason,
                data={
                    "composite": c.composite,
                    "drawdown_pct": c.dip.drawdown_pct,
                },
            )
        )
    return 0


def _sev(name: str) -> Severity:
    if name == "URGENT":
        return Severity.URGENT
    if name == "ALERT":
        return Severity.ALERT
    return Severity.INFO
