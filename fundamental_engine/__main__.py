"""Fundamental analyst CLI — Claude Code native.

Subcommands:
    --hello                          Print resolved config and exit.
    resolve <NAME>                   Name → ticker candidates on any exchange.
    refresh <TICKER>                 Fetch live data + cache score (no report).
    refresh all                      Refresh every cached ticker.
    analyse <TICKER>                 Render report for one ticker.
    inspect <TICKER> [--section X]   Cached score + judgment + full snapshot as JSON.
    screen [--market …] [filters]    Screen every listing in the chosen markets (Yahoo).
    pick --amount <BUDGET>           Buy-the-dip candidates (budget in BASE_CURRENCY).
    brief                            Market overview from cached state.
    research <NAME>                  Fetch Wikipedia + web research for a person.
    news <TICKER>                    Fetch per-company news via Google News RSS.
    engine                           Long-running background cache warmer.
    migrate-ids [--dry-run]          One-time: legacy bare NSE keys → Yahoo symbols.

Tickers are Yahoo symbols on any exchange: MSFT, GOOGL, RELIANCE.NS,
0700.HK, 7203.T, SAP.DE, SHEL.L.

Backtests live outside the app, like tests: `python3 -m backtests`.

Qualitative pillars (leadership / prospects / news) are scored by Claude in
the matching slash commands (`.claude/skills/<name>/SKILL.md`). Claude writes
judgements to `data_cache/judgments/<TICKER>.json` which this CLI reads when
rendering.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import TYPE_CHECKING

from fundamental_engine.analysis_context import (
    analysis_context_panel,
    format_age,
    judgment_age_days,
)
from fundamental_engine.config import AnalystConfig
from signals.fileio import atomic_write_text

if TYPE_CHECKING:
    from fundamental_engine.judgments import JudgmentStore

log = logging.getLogger("fundamental_engine")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fundamental_engine",
        description="Global equity fundamental analyst — Claude Code native.",
    )
    parser.add_argument(
        "--hello",
        action="store_true",
        help="Print resolved config and exit.",
    )
    sub = parser.add_subparsers(dest="command", title="commands")

    p_resolve = sub.add_parser(
        "resolve", help="Name → ticker candidates on any exchange (Yahoo search)."
    )
    p_resolve.add_argument("query", help="Free-text company name.")
    p_resolve.add_argument("--limit", type=int, default=8)
    p_resolve.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a table (for skill consumption).",
    )

    p_refresh = sub.add_parser(
        "refresh", help="Fetch live data and cache score for a ticker or all cached tickers."
    )
    p_refresh.add_argument("target", help="Ticker symbol or 'all'.")
    p_refresh.add_argument(
        "--rejudge",
        action="store_true",
        help="Also delete the cached Claude judgment for the ticker, forcing a fresh /analyse-stock pass.",
    )

    p_analyse = sub.add_parser(
        "analyse", help="Render stock report for one ticker (fetches live)."
    )
    p_analyse.add_argument("ticker")

    p_inspect = sub.add_parser(
        "inspect",
        help="Print the cached score, judgment, and full snapshot for a ticker as JSON.",
    )
    p_inspect.add_argument("ticker")
    p_inspect.add_argument(
        "--section",
        action="append",
        default=[],
        help="Only print this snapshot field (repeatable), e.g. --section raw_profit_loss.",
    )

    _add_screen_parser(sub)

    p_pick = sub.add_parser(
        "pick", help="Buy-the-dip candidates from cached scores."
    )
    p_pick.add_argument(
        "--amount",
        required=True,
        help="Budget in BASE_CURRENCY (e.g. 100000, 50k, 2.5m, 1L).",
    )
    p_pick.add_argument("--min-composite", type=float, default=75.0)
    p_pick.add_argument(
        "--min-verdict",
        default="BUY",
        choices=["STRONG_BUY", "BUY", "HOLD", "AVOID", "RED_FLAG"],
    )
    p_pick.add_argument(
        "--tickers",
        nargs="+",
        default=[],
        help="Only pick among these cached tickers (e.g. the ones `screen --score` cached).",
    )

    sub.add_parser("brief", help="Market overview from cached state.")

    p_research = sub.add_parser(
        "research", help="Fetch Wikipedia + web research for a person."
    )
    p_research.add_argument("name", help="Person name (e.g. 'Mukesh Ambani').")
    p_research.add_argument("--ticker", default="", help="Optional company ticker context.")
    p_research.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a rendered report.",
    )

    p_news = sub.add_parser(
        "news",
        help="Fetch per-company news via Google News RSS. Uses cached company name unless --company is given.",
    )
    p_news.add_argument("ticker", help="Ticker symbol, e.g. MSFT or RELIANCE.NS.")
    p_news.add_argument(
        "--company",
        default="",
        help="Override the company name used in the search query.",
    )
    p_news.add_argument("--days", type=int, default=30, help="Lookback window in days.")
    p_news.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a rendered table.",
    )

    sub.add_parser("engine", help="Run the background cache warmer (long-running).")

    p_migrate = sub.add_parser(
        "migrate-ids",
        help="One-time: re-key legacy bare NSE tickers (RELIANCE) to Yahoo symbols (RELIANCE.NS).",
    )
    p_migrate.add_argument(
        "--dry-run", action="store_true", help="List the renames without changing anything."
    )

    return parser


def _add_screen_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from fundamental_engine.commands.screen import DEFAULT_LIMIT, DEFAULT_SCAN, ScreenSort

    p = sub.add_parser(
        "screen",
        help="Screen every listed stock in the chosen markets (Yahoo's global screener).",
    )
    p.add_argument(
        "--market",
        nargs="+",
        default=[],
        help="Yahoo market codes: us in jp gb hk cn kr de … (default: all 59).",
    )
    p.add_argument("--sector", nargs="+", default=[], help="e.g. Technology 'Financial Services'.")
    p.add_argument("--industry", nargs="+", default=[], help="e.g. Semiconductors.")
    p.add_argument("--min-mcap", help="Minimum market cap in BASE_CURRENCY (e.g. 10B, 500cr).")
    p.add_argument("--max-mcap", help="Maximum market cap in BASE_CURRENCY.")
    p.add_argument("--max-pe", type=float, help="Maximum trailing P/E (loss-makers never pass).")
    p.add_argument("--min-roe", type=float, help="Minimum return on equity, in %%.")
    p.add_argument("--max-de", type=float, help="Maximum debt / equity, as a multiple (0.5).")
    p.add_argument("--min-revenue-growth", type=float, help="Minimum 1-year revenue growth, in %%.")
    p.add_argument("--min-dividend-yield", type=float, help="Minimum forward dividend yield, in %%.")
    p.add_argument(
        "--min-drawdown", type=float, help="Minimum %% below the 52-week high (20 = a 20%% dip)."
    )
    p.add_argument("--sort", choices=[s.value for s in ScreenSort], default=ScreenSort.MARKET_CAP.value)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Rows to show.")
    p.add_argument(
        "--scan",
        type=int,
        default=DEFAULT_SCAN,
        help="Largest matching listings read per market (250 per request).",
    )
    p.add_argument(
        "--score",
        type=int,
        default=0,
        help="Fully fetch and score the top N, adding them to the cache.",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON (for skill consumption).")


def _print_hello() -> int:
    cfg = AnalystConfig.from_env()
    print("Fundamental Analyst — resolved config")
    print(json.dumps(cfg.redacted(), indent=2, default=str))
    return 0


def _run_resolve(query: str, limit: int, as_json: bool) -> int:
    from fundamental_engine.resolver import resolve_name

    matches = asyncio.run(resolve_name(query, limit=limit))
    if not matches:
        print(f"No equity listings match {query!r}.")
        return 3

    if as_json:
        print(json.dumps([m.to_dict() for m in matches], indent=2))
        return 0

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"Matches for {query!r}")
    table.add_column("Ticker", style="bold")
    table.add_column("Exchange")
    table.add_column("Name")
    for m in matches:
        table.add_row(m.ticker, m.exchange, m.long_name or m.short_name)
    console.print(table)
    return 0


def _run_refresh(target: str, *, rejudge: bool = False) -> int:
    from fundamental_engine.engine import Engine, RefreshStatus
    from fundamental_engine.judgments import JudgmentStore
    from fundamental_engine.ticker import canonical

    cfg = AnalystConfig.from_env()
    engine = Engine.from_env()

    if rejudge:
        store = JudgmentStore(cfg.data_cache_dir)
        if target.lower() == "all":
            tickers = store.list_tickers()
            if not tickers:
                print("rejudge: no cached judgments to clear")
            for ticker in tickers:
                age = _judgment_age_days_for(store, ticker)
                store.path_for(ticker).unlink(missing_ok=True)
                age_str = format_age(age)
                print(f"rejudge: cleared {ticker} judgment ({age_str})")
        else:
            bare = canonical(target)
            existing = store.get(bare)
            fp = store.path_for(bare)
            if existing is not None:
                age = judgment_age_days(existing.updated_at)
                age_str = format_age(age)
                fp.unlink()
                print(f"rejudge: cleared {bare} judgment ({age_str})")
            else:
                print(f"rejudge: no prior judgment for {bare} — this will be a fresh analysis")

    if target.lower() == "all":
        results = asyncio.run(engine.refresh_all())
        ok = sum(1 for r in results if r.status == RefreshStatus.OK)
        no_data = sum(1 for r in results if r.status == RefreshStatus.NO_DATA)
        errored = sum(1 for r in results if r.status == RefreshStatus.ERROR)
        print(
            f"Refreshed: {ok} ok, {no_data} no_data, {errored} errors "
            f"(out of {len(results)} tickers)."
        )
        for r in results:
            if r.status == RefreshStatus.OK:
                print(
                    f"  {r.ticker:<12} {r.verdict:<10} composite {r.composite}"
                )
        if not results:
            print(
                "No tickers in cache yet — run `refresh <TICKER>` on a few "
                "first, then `refresh all` to warm them on a schedule."
            )
    else:
        result = asyncio.run(engine.refresh_one(canonical(target)))
        print(
            f"{result.ticker}: {result.status}  "
            f"composite={result.composite}  verdict={result.verdict}  "
            f"{result.detail}"
        )
    return 0


def _run_analyse(ticker: str) -> int:
    from data_sources.fx_source import conversion_rate
    from fundamental_engine.analyse_pipeline import AnalysisResult, NoDataError, analyse_ticker
    from fundamental_engine.cache import JsonStore
    from fundamental_engine.extended_score import ExtendedScore
    from fundamental_engine.snapshot_store import SnapshotStore
    from fundamental_engine.ticker import canonical
    from reports.formatting import make_console
    from reports.stock_report import render_stock_report
    from screener.price_history import PriceHistoryStore

    cfg = AnalystConfig.from_env()
    bare = canonical(ticker)

    async def fetch_and_score() -> tuple[AnalysisResult, float | None]:
        analysed = await analyse_ticker(bare, data_cache_dir=cfg.data_cache_dir)
        fx = await conversion_rate(analysed.snapshot.currency, cfg.base_currency)
        return analysed, fx

    try:
        result, fx_rate = asyncio.run(fetch_and_score())
    except NoDataError as e:
        print(f"[{bare}] {e}")
        print("Check the ticker symbol (use `resolve <NAME>` to look it up).")
        return 3

    JsonStore(cfg.data_cache_dir / "scores.json").upsert(
        bare, ExtendedScore.from_analysis(result, fx_rate_to_base=fx_rate).to_dict()
    )
    SnapshotStore(cfg.data_cache_dir).save(result.snapshot)

    price_rows_written = 0
    if result.price_history:
        with PriceHistoryStore(cfg.data_cache_dir / "prices.db") as store:
            price_rows_written = store.load_rows(bare, result.price_history)

    # Always print an "analysis context" banner up front so the user can
    # see exactly which state this run used — fresh deterministic vs
    # blended with a cached judgment, and how old that judgment was.
    console = make_console()
    console.print(analysis_context_panel(result, bare, price_rows_written))

    reports_dir = cfg.data_cache_dir / "reports"
    fp = render_stock_report(result.snapshot, result.score, output_dir=reports_dir)
    if fp:
        print(f"\nReport saved: {fp}")

    if result.judgment is None:
        print(
            "\n[hint] Leadership / prospects / news pillars are awaiting "
            "Claude judgment. Run `/analyse-stock " + bare + "` in Claude "
            "Code to generate and cache them."
        )
    else:
        age = judgment_age_days(result.judgment.updated_at)
        if age is not None and age >= 3:
            print(
                f"\n[warn] Blended with a {format_age(age)} Claude judgment. "
                f"Run `fundamental_engine refresh {bare} --rejudge` "
                f"followed by `/analyse-stock {bare}` in Claude Code for "
                f"a fresh qualitative pass."
            )
    return 0


def _judgment_age_days_for(store: "JudgmentStore", ticker: str) -> int | None:
    judgment = store.get(ticker)
    if judgment is None:
        return None
    return judgment_age_days(judgment.updated_at)


def _run_inspect(ticker: str, sections: list[str]) -> int:
    """Dump everything cached for one ticker — no network, no re-scoring."""
    from dataclasses import fields

    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from fundamental_engine.judgments import JudgmentStore
    from fundamental_engine.snapshot_store import SnapshotStore
    from fundamental_engine.ticker import canonical
    from fundamental_engine.universe import load_scores

    cfg = AnalystConfig.from_env()
    bare = canonical(ticker)
    extended = load_scores(cfg.data_cache_dir).get(bare)
    judgment = JudgmentStore(cfg.data_cache_dir).get(bare)
    snapshot = SnapshotStore(cfg.data_cache_dir).get(bare)

    if extended is None and judgment is None and snapshot is None:
        print(
            f"[inspect] Nothing cached for {bare}. Run "
            f"`python3 -m fundamental_engine refresh {bare}` first."
        )
        return 3

    snapshot_dict = snapshot.to_dict() if snapshot is not None else None
    if sections:
        known = {f.name for f in fields(BalanceSheetSnapshot)}
        unknown = [name for name in sections if name not in known]
        if unknown:
            print(f"[inspect] Unknown snapshot section(s): {', '.join(unknown)}")
            print("Available: " + ", ".join(sorted(known)))
            return 2
        payload: dict[str, object] = {
            "ticker": bare,
            "snapshot": (
                {name: snapshot_dict.get(name) for name in sections}
                if snapshot_dict is not None
                else None
            ),
        }
    else:
        payload = {
            "ticker": bare,
            "score": extended.to_dict() if extended is not None else None,
            "judgment": judgment.to_dict() if judgment is not None else None,
            "snapshot": snapshot_dict,
        }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _run_screen(args: argparse.Namespace) -> int:
    from fundamental_engine.budget_parser import parse_budget
    from fundamental_engine.commands.screen import ScreenRequest, ScreenSort, run_screen_command

    bounds: list[float | None] = []
    for flag, raw in (("--min-mcap", args.min_mcap), ("--max-mcap", args.max_mcap)):
        amount = parse_budget(raw) if raw else None
        if raw and amount is None:
            print(f"[screen] Invalid {flag}: {raw!r}. Try '10B', '500cr', '2.5m'.")
            return 2
        bounds.append(amount)

    request = ScreenRequest(
        markets=tuple(m.lower() for m in args.market),
        sectors=tuple(args.sector),
        industries=tuple(args.industry),
        max_pe=args.max_pe,
        min_roe_pct=args.min_roe,
        max_debt_to_equity=args.max_de,
        min_revenue_growth_pct=args.min_revenue_growth,
        min_dividend_yield_pct=args.min_dividend_yield,
        min_market_cap=bounds[0],
        max_market_cap=bounds[1],
        min_drawdown_pct=args.min_drawdown,
        sort=ScreenSort(args.sort),
        limit=args.limit,
        scan=args.scan,
        score=args.score,
    )
    return run_screen_command(request, as_json=args.json)


def _run_pick(
    amount_raw: str, min_composite: float, min_verdict: str, tickers: list[str]
) -> int:
    from fundamental_engine.commands.pick import run_pick_command

    return run_pick_command(
        amount_raw,
        min_composite=min_composite,
        min_verdict=min_verdict,
        tickers=tickers or None,
    )


def _run_brief() -> int:
    from fundamental_engine.commands.brief import run_brief_command

    return run_brief_command()


def _run_research(name: str, ticker: str, as_json: bool) -> int:
    from data_sources.research_source import ResearchSource
    from fundamental_engine.ticker import slug
    from reports.formatting import make_console
    from rich.panel import Panel

    cfg = AnalystConfig.from_env()
    bundle = asyncio.run(
        ResearchSource(brave_api_key=cfg.brave_api_key).research(name, ticker)
    )

    research_dir = cfg.data_cache_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    fp = research_dir / f"{slug(name)}.json"
    atomic_write_text(fp, json.dumps(bundle.to_dict(), indent=2, default=str))

    if as_json:
        print(json.dumps(bundle.to_dict(), indent=2, default=str))
        return 0

    console = make_console()
    console.print(
        Panel(
            f"[bold]{name}[/bold]"
            + (f" — {ticker}" if ticker else "")
            + f"\nSources: {', '.join(bundle.sources) or '(none)'}"
            + f"\nCached at: {fp}",
            title="Research fetched",
            title_align="left",
        )
    )
    if bundle.wikipedia_summary:
        console.print(
            Panel(
                bundle.wikipedia_summary,
                title=f"Wikipedia ({bundle.wikipedia_url})",
                title_align="left",
                border_style="dim",
            )
        )
    if bundle.web_hits:
        lines = []
        for hit in bundle.web_hits[:10]:
            lines.append(f"• {hit.get('title', '')}")
            snippet = hit.get("snippet", "")
            if snippet:
                lines.append(f"  {snippet}")
            url = hit.get("url", "")
            if url:
                lines.append(f"  [dim]{url}[/dim]")
        console.print(
            Panel(
                "\n".join(lines),
                title="Web hits",
                title_align="left",
                border_style="dim",
            )
        )
    if not bundle.wikipedia_summary and not bundle.web_hits:
        console.print(
            "[yellow]No research data found. "
            "Set BRAVE_API_KEY for web search support.[/yellow]"
        )
    return 0


def _run_news(ticker: str, company_override: str, days: int, as_json: bool) -> int:
    from data_sources.news_source import NewsSource
    from fundamental_engine.ticker import canonical
    from fundamental_engine.universe import load_scores
    from reports.formatting import make_console
    from rich.table import Table

    cfg = AnalystConfig.from_env()
    bare = canonical(ticker)

    company_name = company_override.strip()
    if not company_name:
        cached = load_scores(cfg.data_cache_dir).get(bare)
        company_name = cached.company_name if cached is not None else ""

    if not company_name:
        print(
            f"[news] No company name known for {bare}. Either pass "
            f"--company \"Full Company Name\" or run "
            f"`python3 -m fundamental_engine refresh {bare}` first."
        )
        return 3

    items = asyncio.run(
        NewsSource().fetch_for_company(
            company_name, ticker=bare, within_days=days
        )
    )

    news_dir = cfg.data_cache_dir / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    fp = news_dir / f"{bare}.json"
    atomic_write_text(
        fp, json.dumps([i.to_dict() for i in items], indent=2, default=str)
    )

    if as_json:
        print(json.dumps([i.to_dict() for i in items], indent=2, default=str))
        return 0

    console = make_console()
    table = Table(
        title=f"News for {bare} — {company_name} ({len(items)} items, last {days}d)"
    )
    table.add_column("Published", style="dim")
    table.add_column("Source")
    table.add_column("Cat")
    table.add_column("Headline")
    for item in items[:25]:
        table.add_row(
            (item.published_at or "")[:10],
            item.source[:20],
            item.material_category if item.is_material else "",
            (item.headline or "")[:90],
        )
    console.print(table)
    console.print(f"\n[dim]Cached: {fp}[/dim]")
    if not items:
        console.print(
            "[yellow]No items in window. Google News may have nothing "
            "recent for this query.[/yellow]"
        )
    return 0


def _run_engine() -> int:
    from fundamental_engine.engine import Engine

    engine = Engine.from_env()
    try:
        asyncio.run(engine.run_forever())
    except KeyboardInterrupt:
        print("\nengine stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.hello or args.command is None:
        return _print_hello()

    if args.command == "resolve":
        return _run_resolve(args.query, args.limit, args.json)
    if args.command == "refresh":
        return _run_refresh(args.target, rejudge=args.rejudge)
    if args.command == "analyse":
        return _run_analyse(args.ticker)
    if args.command == "inspect":
        return _run_inspect(args.ticker, args.section)
    if args.command == "screen":
        return _run_screen(args)
    if args.command == "pick":
        return _run_pick(args.amount, args.min_composite, args.min_verdict, args.tickers)
    if args.command == "brief":
        return _run_brief()
    if args.command == "research":
        return _run_research(args.name, args.ticker, args.json)
    if args.command == "news":
        return _run_news(args.ticker, args.company, args.days, args.json)
    if args.command == "engine":
        return _run_engine()
    if args.command == "migrate-ids":
        from fundamental_engine.commands.migrate import run_migrate_command

        return run_migrate_command(dry_run=args.dry_run)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
