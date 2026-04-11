"""Fundamental analyst CLI — Claude Code native.

Subcommands:
    --hello                          Print resolved config and exit.
    resolve <NAME>                   Name → ticker candidates (yfinance search).
    refresh <TICKER>                 Fetch live data + cache score (no report).
    refresh all                      Refresh every cached ticker.
    analyse <TICKER>                 Render report for one ticker.
    pick --amount <INR>              Buy-the-dip candidates from cached scores.
    brief                            Market overview from cached state.
    research <NAME>                  Fetch Wikipedia + web research for a person.
    news <TICKER>                    Fetch per-company news via Google News RSS.
    engine                           Long-running background cache warmer.

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

from fundamental_engine.config import AnalystConfig

log = logging.getLogger("fundamental_engine")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fundamental_engine",
        description="Indian equity fundamental analyst — Claude Code native.",
    )
    parser.add_argument(
        "--hello",
        action="store_true",
        help="Print resolved config and exit.",
    )
    sub = parser.add_subparsers(dest="command", title="commands")

    p_resolve = sub.add_parser(
        "resolve", help="Name → ticker candidates via yfinance search."
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

    p_analyse = sub.add_parser(
        "analyse", help="Render stock report for one ticker (fetches live)."
    )
    p_analyse.add_argument("ticker")

    p_pick = sub.add_parser(
        "pick", help="Buy-the-dip candidates from cached scores."
    )
    p_pick.add_argument("--amount", required=True, help="Budget in INR (e.g. 100000 or 1L).")
    p_pick.add_argument("--min-composite", type=float, default=75.0)
    p_pick.add_argument(
        "--min-verdict",
        default="BUY",
        choices=["STRONG_BUY", "BUY", "HOLD", "AVOID", "RED_FLAG"],
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
    p_news.add_argument("ticker", help="NSE/BSE ticker symbol.")
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

    return parser


def _print_hello() -> int:
    cfg = AnalystConfig.from_env()
    print("Fundamental Analyst — resolved config")
    print(json.dumps(cfg.redacted(), indent=2, default=str))
    return 0


def _run_resolve(query: str, limit: int, as_json: bool) -> int:
    from fundamental_engine.resolver import resolve_name

    matches = asyncio.run(resolve_name(query, limit=limit))
    if not matches:
        print(f"No Indian-exchange matches for {query!r}.")
        return 3

    if as_json:
        print(json.dumps([m.to_dict() for m in matches], indent=2))
        return 0

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"Matches for {query!r}")
    table.add_column("Ticker", style="bold")
    table.add_column("Symbol")
    table.add_column("Exchange")
    table.add_column("Name")
    for m in matches:
        table.add_row(m.ticker, m.symbol, m.exchange, m.long_name or m.short_name)
    console.print(table)
    return 0


def _run_refresh(target: str) -> int:
    from fundamental_engine.engine import Engine

    engine = Engine.from_env()
    if target.lower() == "all":
        results = asyncio.run(engine.refresh_all())
        ok = sum(1 for r in results if r.status == "ok")
        no_data = sum(1 for r in results if r.status == "no_data")
        errored = sum(1 for r in results if r.status == "error")
        print(
            f"Refreshed: {ok} ok, {no_data} no_data, {errored} errors "
            f"(out of {len(results)} tickers)."
        )
        for r in results:
            if r.status == "ok":
                print(
                    f"  {r.ticker:<12} {r.verdict:<10} composite {r.composite}"
                )
        if not results:
            print(
                "No tickers in cache yet — run `refresh <TICKER>` on a few "
                "first, then `refresh all` to warm them on a schedule."
            )
    else:
        result = asyncio.run(engine.refresh_one(target))
        print(
            f"{result.ticker}: {result.status}  "
            f"composite={result.composite}  verdict={result.verdict}  "
            f"{result.detail}"
        )
    return 0


def _run_analyse(ticker: str) -> int:
    from fundamental_engine.analyse_pipeline import (
        NoDataError,
        analyse_ticker,
        build_extended_score_dict,
    )
    from fundamental_engine.cache import JsonStore
    from fundamental_engine.ticker import canonical
    from reports.stock_report import render_stock_report
    from screener.price_history import PriceHistoryStore

    cfg = AnalystConfig.from_env()
    bare = canonical(ticker)

    try:
        result = asyncio.run(
            analyse_ticker(bare, data_cache_dir=cfg.data_cache_dir)
        )
    except NoDataError as e:
        print(f"[{bare}] {e}")
        print("Check the ticker symbol (use `resolve <NAME>` to look it up).")
        return 3

    JsonStore(cfg.data_cache_dir / "scores.json").upsert(
        bare, build_extended_score_dict(result)
    )

    if result.price_history:
        with PriceHistoryStore(cfg.data_cache_dir / "prices.db") as store:
            store.load_rows(bare, result.price_history)

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
    return 0


def _run_pick(amount_raw: str, min_composite: float, min_verdict: str) -> int:
    from reports.picks_report import run_pick_command

    return run_pick_command(
        amount_raw, min_composite=min_composite, min_verdict=min_verdict
    )


def _run_brief() -> int:
    from reports.market_brief import run_brief_command

    return run_brief_command()


def _run_research(name: str, ticker: str, as_json: bool) -> int:
    from data_sources.research_source import ResearchSource
    from fundamental_engine.ticker import slug
    from reports.formatting import make_console
    from rich.panel import Panel

    cfg = AnalystConfig.from_env()
    bundle = asyncio.run(ResearchSource().research(name, ticker))

    research_dir = cfg.data_cache_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    fp = research_dir / f"{slug(name)}.json"
    fp.write_text(json.dumps(bundle.to_dict(), indent=2, default=str))

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
    from fundamental_engine.cache import JsonStore
    from fundamental_engine.ticker import canonical
    from reports.formatting import make_console
    from rich.table import Table

    cfg = AnalystConfig.from_env()
    bare = canonical(ticker)

    company_name = company_override.strip()
    if not company_name:
        scores = JsonStore(cfg.data_cache_dir / "scores.json").read()
        entry = scores.get(bare) or {}
        company_name = entry.get("company_name") or ""

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
    fp.write_text(
        json.dumps([i.to_dict() for i in items], indent=2, default=str)
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
        return _run_refresh(args.target)
    if args.command == "analyse":
        return _run_analyse(args.ticker)
    if args.command == "pick":
        return _run_pick(args.amount, args.min_composite, args.min_verdict)
    if args.command == "brief":
        return _run_brief()
    if args.command == "research":
        return _run_research(args.name, args.ticker, args.json)
    if args.command == "news":
        return _run_news(args.ticker, args.company, args.days, args.json)
    if args.command == "engine":
        return _run_engine()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
