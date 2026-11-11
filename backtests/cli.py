"""`python3 -m backtests` — test the scoring's claims against history.

    python3 -m backtests [SUITE …] [--tickers SYMBOL … | all] [--years 30]
                         [--step year|quarter] [--horizon MONTHS] [--lag DAYS]
    python3 -m backtests --list

Every selected suite reads the same observations: each ticker's history is
fetched once (SEC EDGAR for US filers, screener.in for Indian listings,
Yahoo for prices and everything else) and scored on every rebalance date.

The universe is one listing per company. A second share class or a
depositary receipt of a company already in it is the same business scored
twice — more observations, no more evidence — and a cached quote whose
price and statements don't reconcile is a receipt whose returns say
nothing about the scored business. Both are left out, and named.

Exit code 1 when a suite FAILs (the data significantly contradicts its
claim), else 0 — like a test run.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import date, datetime

from rich.console import Console

from backtests.engine.runner import run_backtest
from backtests.engine.schedule import Step, rebalance_dates
from backtests.report import BacktestReport, SuiteRun, render_markdown, render_terminal
from backtests.suite import Suite
from backtests.suites import ALL
from data_sources.fundamentals_source.history import (
    DEFAULT_REPORTING_LAG_DAYS,
    StatementHistory,
    fetch_statement_history,
)
from data_sources.fx_source import major_unit
from data_sources.names import clean_company_name
from fundamental_engine.config import AnalystConfig
from fundamental_engine.snapshot_store import SnapshotStore
from fundamental_engine.ticker import canonical
from fundamental_engine.universe import load_universe
from reports.formatting import make_console
from signals.fileio import atomic_write_text

FETCH_SPACING_SECONDS = 1.0  # stay polite to screener.in and SEC EDGAR

EXIT_OK = 0
EXIT_SUITE_FAILED = 1
EXIT_UNKNOWN_SUITE = 2
EXIT_NO_TICKERS = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m backtests",
        description="Test each claim the scoring makes against point-in-time history.",
    )
    parser.add_argument("suites", nargs="*", help="Suites to run (default: all). See --list.")
    parser.add_argument("--list", action="store_true", help="List the suites and their claims.")
    parser.add_argument(
        "--tickers", nargs="+", default=["all"],
        help="Yahoo symbols (MSFT RELIANCE.NS 0700.HK …) or 'all' for the cached universe.",
    )
    parser.add_argument("--years", type=int, default=30, help="How far back (default 30).")
    parser.add_argument(
        "--step", choices=[s.value for s in Step], default=Step.YEAR.value,
        help="Rebalance every year or quarter.",
    )
    parser.add_argument(
        "--horizon", type=int, default=None, help="Forward-return window in months (default: the step)."
    )
    parser.add_argument(
        "--lag", type=int, default=DEFAULT_REPORTING_LAG_DAYS,
        help="Days after a fiscal year ends before its numbers count as public "
        f"(default {DEFAULT_REPORTING_LAG_DAYS}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for suite in ALL:
            print(f"{suite.name:<20} {suite.claim}")
        return EXIT_OK

    suites = select_suites(args.suites)
    if suites is None:
        known = ", ".join(s.name for s in ALL)
        print(f"[backtests] Unknown suite in {args.suites} — choose from: {known}")
        return EXIT_UNKNOWN_SUITE

    cfg = AnalystConfig.from_env()
    tickers = resolve_tickers(args.tickers, cfg)
    if not tickers:
        print("[backtests] No tickers — pass --tickers MSFT RELIANCE.NS …, "
              "or analyse some first for 'all'.")
        return EXIT_NO_TICKERS

    step = Step(args.step)
    horizon = args.horizon or step.months
    console = make_console()
    fetched = asyncio.run(fetch_histories(tickers, cfg.sec_user_agent, console))
    histories, duplicates = one_listing_per_company(fetched)
    if duplicates:
        console.print(
            f"[dim]One listing per company — left out: {', '.join(duplicates)}[/dim]"
        )
    result = run_backtest(
        histories,
        rebalance_dates(end=date.today(), years=args.years, step=step),
        horizon_months=horizon,
        reporting_lag_days=args.lag,
    )
    report = BacktestReport(
        settings=(
            f"{len(histories)} tickers · {args.years}y · {step.value}ly rebalance · "
            f"{horizon}-month forward total return · {args.lag}-day reporting lag"
        ),
        result=result,
        runs=[SuiteRun(suite=suite, result=suite.evaluate(result.observations)) for suite in suites],
    )

    render_terminal(console, report)
    path = cfg.data_cache_dir / "reports" / f"backtest_{datetime.now().strftime('%Y-%m-%d_%H%M')}.md"
    atomic_write_text(path, render_markdown(report))
    console.print(f"\n[dim]Report saved: {path}[/dim]")
    return EXIT_SUITE_FAILED if report.any_failed else EXIT_OK


def select_suites(names: Sequence[str]) -> list[Suite] | None:
    """The named suites in report order (all when none named); None if a name is unknown."""
    if not names:
        return list(ALL)
    by_name = {suite.name: suite for suite in ALL}
    if any(name not in by_name for name in names):
        return None
    return [suite for suite in ALL if suite.name in names]


def resolve_tickers(targets: Sequence[str], cfg: AnalystConfig) -> list[str]:
    """The named tickers, or for 'all' every cached ticker whose quote reconciles."""
    if [t.lower() for t in targets] != ["all"]:
        return [canonical(t) for t in targets]
    snapshots = SnapshotStore(cfg.data_cache_dir)
    cached = load_universe(cfg.data_cache_dir)
    receipts = [
        ticker for ticker in cached
        if (snapshot := snapshots.get(ticker)) is not None and snapshot.quote_unreliable
    ]
    if receipts:
        print(f"[backtests] Left out (quote doesn't reconcile with the statements): "
              f"{', '.join(receipts)}")
    return [ticker for ticker in cached if ticker not in receipts]


def one_listing_per_company(
    histories: Sequence[StatementHistory],
) -> tuple[list[StatementHistory], list[str]]:
    """Each company once — and the symbols left out.

    The listing kept is the one priced in the company's own reporting
    currency, then the one with the longest price history (GOOG over a
    peso-priced receipt of it; one of GOOG / GOOGL). A listing with no
    company name can't be matched to another and is always kept.
    """
    best: dict[str, StatementHistory] = {}
    for history in histories:
        company = _company_key(history)
        rival = best.get(company)
        if rival is None or _listing_rank(history) > _listing_rank(rival):
            best[company] = history
    kept = [history for history in histories if best[_company_key(history)] is history]
    left_out = [history.symbol for history in histories if best[_company_key(history)] is not history]
    return kept, left_out


def _company_key(history: StatementHistory) -> str:
    name = history.metadata.get("company_name")
    return clean_company_name(name).lower() if name else history.symbol


def _listing_rank(history: StatementHistory) -> tuple[bool, int]:
    """(priced in the reporting currency, days of price history) — higher is preferred."""
    trading = history.metadata.get("currency")
    reporting = history.metadata.get("financial_currency")
    home = bool(trading and reporting) and major_unit(trading)[0] == reporting.upper()
    return home, len(history.prices)


async def fetch_histories(
    tickers: Sequence[str], sec_user_agent: str, console: Console
) -> list[StatementHistory]:
    histories: list[StatementHistory] = []
    for i, ticker in enumerate(tickers):
        if i:
            await asyncio.sleep(FETCH_SPACING_SECONDS)
        history = await fetch_statement_history(ticker, sec_user_agent=sec_user_agent)
        console.print(
            f"[dim]fetched {ticker:<14} fundamentals: {history.fundamentals_source:<18} "
            f"prices: {len(history.prices):>6} closes[/dim]"
        )
        histories.append(history)
    return histories
