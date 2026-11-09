"""`screen` — stocks anywhere in the world that pass the asked filters.

The cache holds only what has been analysed; `screen` reads whole markets.
Three stages, cheapest first:

  1. Yahoo's screener, one market at a time (`data_sources.universe_source`):
     the ratio filters — P/E, ROE, debt / equity, revenue growth, dividend
     yield — run server-side over every listing, largest first. Pages are
     read until `--scan` tradeable listings are in hand (see `_tradeable`:
     depositary receipts and foreign copies are skipped).
  2. Here: market cap in the base currency (live FX per listing), distance
     below the 52-week high, and one listing per company — the most traded
     one, so RELIANCE.NS beats RELIANCE.BO.
  3. `--score N`: the top N go through the same live fetch and deterministic
     scoring as `refresh` and join the cache, so `pick --tickers`, `brief`
     and `/analyse-stock` can use them.

Market-cap bounds are given in the base currency. Yahoo filters amounts in
each market's own currency, so a bound is converted with the currency of
the market's largest tradeable listings (one probe request per market),
then every listing is checked again exactly, in its own currency.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, replace
from enum import StrEnum

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_sources import universe_source
from data_sources.fx_source import conversion_rate, major_unit
from data_sources.names import clean_company_name
from data_sources.universe_source import PAGE_SIZE, Criteria, Listing
from fundamental_engine.config import AnalystConfig
from fundamental_engine.engine import Engine, RefreshResult, RefreshStatus
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.universe import load_scores
from reports.formatting import VERDICT_STYLES, format_money, format_price, make_console

log = logging.getLogger(__name__)

DEFAULT_SCAN = PAGE_SIZE  # tradeable listings kept per market, largest first
MAX_PAGES_PER_MARKET = 4  # 1,000 raw listings: Brazil's and Germany's top pages are mostly receipts
DEFAULT_LIMIT = 25  # rows shown
PROBE_SIZE = 100  # listings sampled to learn a market's currency
MIN_DAILY_TURNOVER = 1e-5  # 0.001% of market cap traded a day — see `_tradeable`
REQUEST_SPACING_SECONDS = 0.5
SCORE_SPACING_SECONDS = 1.0


class ScreenSort(StrEnum):
    MARKET_CAP = "mcap"
    DRAWDOWN = "drawdown"
    EARNINGS_YIELD = "earnings-yield"


@dataclass(frozen=True, slots=True)
class ScreenRequest:
    markets: tuple[str, ...] = ()  # empty → every market Yahoo screens
    sectors: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    max_pe: float | None = None
    min_roe_pct: float | None = None
    max_debt_to_equity: float | None = None
    min_revenue_growth_pct: float | None = None
    min_dividend_yield_pct: float | None = None
    min_market_cap: float | None = None  # base currency
    max_market_cap: float | None = None  # base currency
    min_drawdown_pct: float | None = None
    sort: ScreenSort = ScreenSort.MARKET_CAP
    limit: int = DEFAULT_LIMIT
    scan: int = DEFAULT_SCAN
    score: int = 0

    def criteria(self, market: str) -> Criteria:
        """The server-side filters for one market (market-cap bounds added later)."""
        return Criteria(
            market=market,
            sectors=self.sectors,
            industries=self.industries,
            max_pe=self.max_pe,
            min_roe_pct=self.min_roe_pct,
            max_debt_to_equity=self.max_debt_to_equity,
            min_revenue_growth_pct=self.min_revenue_growth_pct,
            min_dividend_yield_pct=self.min_dividend_yield_pct,
        )

    @property
    def bounds_market_cap(self) -> bool:
        return self.min_market_cap is not None or self.max_market_cap is not None


@dataclass(frozen=True, slots=True)
class Match:
    listing: Listing
    market: str
    market_cap_base: float
    traded_value_base: float | None  # an average day's trading, base currency

    @property
    def earnings_yield_pct(self) -> float | None:
        pe = self.listing.trailing_pe
        return round(100 / pe, 2) if pe else None

    def to_dict(self) -> dict[str, object]:
        listing = self.listing
        return {
            "ticker": listing.symbol,
            "name": listing.name,
            "market": self.market,
            "exchange": listing.exchange,
            "currency": listing.currency,
            "price": listing.price,
            "market_cap_base": round(self.market_cap_base),
            "drawdown_pct": listing.drawdown_pct,
            "trailing_pe": listing.trailing_pe,
            "traded_value_base": (
                round(self.traded_value_base) if self.traded_value_base is not None else None
            ),
        }


@dataclass(slots=True)
class ScreenResult:
    matches: list[Match]  # one per company, ranked
    markets_read: int
    listings_read: int
    untradeable: int  # receipts, foreign copies, and listings that barely trade
    without_fx: int  # listings dropped: no FX quote for their currency


class _Rates:
    """Base-currency rates per major currency, fetched once per screen."""

    def __init__(self, base: str) -> None:
        self._base = base
        self._cache: dict[str, float | None] = {}

    async def per_unit(self, major_currency: str) -> float | None:
        if major_currency not in self._cache:
            self._cache[major_currency] = await conversion_rate(major_currency, self._base)
        return self._cache[major_currency]


# ── Stages 1 + 2: screen ─────────────────────────────────────────────────────


async def screen(request: ScreenRequest, *, base_currency: str) -> ScreenResult:
    rates = _Rates(base_currency)
    markets = request.markets or tuple(universe_source.markets())
    read: list[tuple[str, Listing]] = []
    untradeable = 0
    for i, market in enumerate(markets):
        if i:
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
        listings, skipped = await _read_market(market, request, rates)
        read.extend((market, listing) for listing in listings)
        untradeable += skipped

    matches: list[Match] = []
    without_fx = 0
    for market, listing in read:
        rate = await rates.per_unit(major_unit(listing.currency)[0])
        if rate is None:
            without_fx += 1
            continue
        match = _match(listing, market, rate, request)
        if match is not None:
            matches.append(match)
    return ScreenResult(
        matches=_rank(_one_per_company(matches), request.sort),
        markets_read=len(markets),
        listings_read=len(read) + untradeable,
        untradeable=untradeable,
        without_fx=without_fx,
    )


async def _read_market(
    market: str, request: ScreenRequest, rates: _Rates
) -> tuple[list[Listing], int]:
    """Up to `request.scan` tradeable listings, largest first, and how many were skipped."""
    criteria = request.criteria(market)
    if request.bounds_market_cap:
        bounded = await _with_local_bounds(criteria, request, rates)
        if bounded is None:
            return [], 0
        criteria = bounded

    kept: list[Listing] = []
    skipped = 0
    for page_number in range(MAX_PAGES_PER_MARKET):
        if page_number:
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
        page = await universe_source.fetch_page(criteria, offset=page_number * PAGE_SIZE)
        for listing in page.listings:
            if _tradeable(listing):
                kept.append(listing)
            else:
                skipped += 1
        read_all = (page_number + 1) * PAGE_SIZE >= page.total
        if len(kept) >= request.scan or not page.listings or read_all:
            break
    log.info("screen: %s — %d tradeable listings (%d skipped)", market, len(kept), skipped)
    return kept[: request.scan], skipped


def _tradeable(listing: Listing) -> bool:
    """Trades at least 0.001% of its market cap on an average day.

    Depositary receipts and foreign copies — Brazil's BDRs, Canada's CDRs,
    Frankfurt and Zurich copies of US stocks — carry the whole company's
    market cap but trade next to nothing (0.0000001%–0.0001% a day), and
    they crowd the top of their markets: 236 of Brazil's 246 largest
    "listings" are BDRs. Primary listings trade 0.001% or more; even Saudi
    Aramco, with a 2% free float, trades 0.003%. Below the floor the market
    cap describes another instrument, and the quote can't be dealt at.
    """
    turnover = listing.daily_turnover
    return turnover is not None and turnover >= MIN_DAILY_TURNOVER


async def _with_local_bounds(
    criteria: Criteria, request: ScreenRequest, rates: _Rates
) -> Criteria | None:
    """Base-currency market-cap bounds → the market's own currency; None skips it."""
    probe = await universe_source.fetch_page(criteria, size=PROBE_SIZE)
    # London's largest "listings" include euro-quoted copies of US stocks,
    # so the market's currency comes from the listings that really trade.
    sample = [listing for listing in probe.listings if _tradeable(listing)] or probe.listings
    if not sample:
        return None
    currency = Counter(major_unit(listing.currency)[0] for listing in sample).most_common(1)[0][0]
    rate = await rates.per_unit(currency)
    if rate is None:
        log.warning("screen: %s skipped — no FX rate for %s", criteria.market, currency)
        return None
    await asyncio.sleep(REQUEST_SPACING_SECONDS)
    return replace(
        criteria,
        min_market_cap=None if request.min_market_cap is None else request.min_market_cap / rate,
        max_market_cap=None if request.max_market_cap is None else request.max_market_cap / rate,
    )


def _match(listing: Listing, market: str, rate: float, request: ScreenRequest) -> Match | None:
    """The exact checks, with `rate` = base currency per unit of the listing's major currency."""
    market_cap = listing.market_cap * rate
    if request.min_market_cap is not None and market_cap < request.min_market_cap:
        return None
    if request.max_market_cap is not None and market_cap > request.max_market_cap:
        return None
    if request.min_drawdown_pct is not None and (
        listing.drawdown_pct is None or listing.drawdown_pct < request.min_drawdown_pct
    ):
        return None
    # Yahoo's screener and its quotes compute P/E from slightly different
    # EPS; the row shown must keep the filter's promise.
    if request.max_pe is not None and (listing.trailing_pe or 0) > request.max_pe:
        return None
    turnover = listing.daily_turnover
    return Match(
        listing=listing,
        market=market,
        market_cap_base=market_cap,
        traded_value_base=turnover * market_cap if turnover is not None else None,
    )


def _one_per_company(matches: list[Match]) -> list[Match]:
    """Keep each company's most traded listing: NSE over BSE, Nasdaq over Frankfurt."""
    best: dict[str, Match] = {}
    for match in matches:
        key = clean_company_name(match.listing.name).lower() or match.listing.symbol
        current = best.get(key)
        if current is None or (match.traded_value_base or 0) > (current.traded_value_base or 0):
            best[key] = match
    return list(best.values())


def _rank(matches: list[Match], sort: ScreenSort) -> list[Match]:
    """Best first; a listing missing the sort value goes last."""
    def value(match: Match) -> float | None:
        if sort is ScreenSort.DRAWDOWN:
            return match.listing.drawdown_pct
        if sort is ScreenSort.EARNINGS_YIELD:
            return match.earnings_yield_pct
        return match.market_cap_base

    return sorted(matches, key=lambda m: (value(m) is not None, value(m) or 0.0), reverse=True)


# ── Stage 3: score the top N ─────────────────────────────────────────────────


async def score_top(matches: list[Match], count: int, engine: Engine) -> list[RefreshResult]:
    """Full live fetch + deterministic scoring for the top `count`, cached like `refresh`."""
    results: list[RefreshResult] = []
    for i, match in enumerate(matches[:count]):
        if i:
            await asyncio.sleep(SCORE_SPACING_SECONDS)
        results.append(await engine.refresh_one(match.listing.symbol))
    return results


# ── Command ──────────────────────────────────────────────────────────────────


def run_screen_command(request: ScreenRequest, *, as_json: bool = False) -> int:
    problem = _invalid(request)
    if problem:
        print(f"[screen] {problem}")
        return 2

    cfg = AnalystConfig.from_env()
    result = asyncio.run(screen(request, base_currency=cfg.base_currency))
    scored: list[RefreshResult] = []
    if request.score > 0 and result.matches:
        scored = asyncio.run(score_top(result.matches, request.score, Engine(cfg)))

    if as_json:
        print(json.dumps(_payload(result, scored, request, cfg.base_currency), indent=2))
        return 0
    _render(
        result,
        scored,
        load_scores(cfg.data_cache_dir),
        request,
        base_currency=cfg.base_currency,
        console=make_console(),
    )
    return 0


def _invalid(request: ScreenRequest) -> str:
    for label, given, valid in (
        ("market", request.markets, universe_source.markets),
        ("sector", request.sectors, universe_source.sectors),
        ("industry", request.industries, universe_source.industries),
    ):
        if not given:
            continue
        accepted = valid()
        unknown = [value for value in given if value not in accepted]
        if unknown:
            return f"Unknown {label}(s): {', '.join(unknown)}. Valid: {', '.join(accepted)}"
    if min(request.scan, request.limit) < 1 or request.score < 0:
        return "--scan and --limit must be at least 1, --score at least 0."
    return ""


def _payload(
    result: ScreenResult, scored: list[RefreshResult], request: ScreenRequest, base: str
) -> dict[str, object]:
    return {
        "base_currency": base,
        "markets_read": result.markets_read,
        "listings_read": result.listings_read,
        "companies_matched": len(result.matches),
        "untradeable": result.untradeable,
        "without_fx": result.without_fx,
        "matches": [match.to_dict() for match in result.matches[: request.limit]],
        "scored": [r.to_dict() for r in scored],
    }


def _render(
    result: ScreenResult,
    scored: list[RefreshResult],
    scores: dict[str, ExtendedScore],
    request: ScreenRequest,
    *,
    base_currency: str,
    console: Console,
) -> None:
    markets = (
        ", ".join(request.markets) if request.markets else f"all {result.markets_read} markets"
    )
    lines = [
        f"Read {result.listings_read:,} listings in {markets}  ·  "
        f"{len(result.matches):,} companies match",
        "Filters: " + ("  ·  ".join(_filter_labels(request, base_currency)) or "none"),
    ]
    if result.untradeable:
        lines.append(
            f"[dim]Skipped {result.untradeable:,} depositary receipts, foreign copies and "
            f"listings trading < 0.001% of their market cap a day[/dim]"
        )
    if result.without_fx:
        lines.append(
            f"[yellow]{result.without_fx} listing(s) dropped — no FX quote for their "
            f"currency[/yellow]"
        )
    console.print(Panel("\n".join(lines), title="Global screen", title_align="left"))

    if not result.matches:
        console.print(
            "[yellow]Nothing matched. Loosen a filter, add markets, or raise --scan "
            "to read deeper into each market.[/yellow]"
        )
        return

    table = Table(title=f"Top {min(request.limit, len(result.matches))} by {request.sort}")
    for column, justify in (
        ("#", "right"), ("Ticker", "left"), ("Company", "left"), ("Exchange", "left"),
        ("Price", "right"), ("Market cap", "right"), ("Below 52w high", "right"), ("P/E", "right"),
    ):
        table.add_column(column, justify=justify)
    for rank, match in enumerate(result.matches[: request.limit], start=1):
        listing = match.listing
        table.add_row(
            str(rank),
            f"[bold]{listing.symbol}[/bold]",
            listing.name[:34],
            listing.exchange,
            format_price(listing.price, listing.currency),
            format_money(match.market_cap_base, base_currency),
            f"{listing.drawdown_pct:.0f}%" if listing.drawdown_pct is not None else "—",
            f"{listing.trailing_pe:.1f}" if listing.trailing_pe is not None else "—",
        )
    console.print(table)

    if scored:
        _render_scored(scored, scores, console)


def _render_scored(
    scored: list[RefreshResult], scores: dict[str, ExtendedScore], console: Console
) -> None:
    table = Table(title="Scored — live statements, deterministic pillars")
    for column, justify in (
        ("Ticker", "left"), ("Country", "left"), ("Fundamentals", "right"),
        ("Valuation", "right"), ("Composite", "right"), ("Verdict", "left"),
    ):
        table.add_column(column, justify=justify)
    ok: list[str] = []
    for result in scored:
        extended = scores.get(result.ticker)
        if result.status != RefreshStatus.OK or extended is None:
            table.add_row(result.ticker, "—", "—", "—", "—", f"[dim]{result.status}[/dim]")
            continue
        ok.append(result.ticker)
        score = extended.score
        style = VERDICT_STYLES.get(score.verdict, "white")
        table.add_row(
            f"[bold]{result.ticker}[/bold]",
            extended.country or "—",
            f"{score.fundamentals_score:.1f}",
            f"{score.valuation_score:.1f}",
            f"{score.composite:.1f}",
            f"[{style}]{score.verdict}[/{style}]",
        )
    console.print(table)
    console.print(
        "[dim]Leadership, prospects and news sit at a neutral 50 until "
        "/analyse-stock judges them; pick's default bar (75) needs those judgments.[/dim]"
    )
    if ok:
        console.print(f"Cached: {' '.join(ok)}")
        console.print(f"Pick from them: pick --amount <BUDGET> --tickers {' '.join(ok)}")


def _filter_labels(request: ScreenRequest, base_currency: str) -> list[str]:
    labels: list[str] = []
    if request.sectors:
        labels.append("sector " + " / ".join(request.sectors))
    if request.industries:
        labels.append("industry " + " / ".join(request.industries))
    if request.min_market_cap is not None:
        labels.append(f"market cap ≥ {format_money(request.min_market_cap, base_currency)}")
    if request.max_market_cap is not None:
        labels.append(f"market cap ≤ {format_money(request.max_market_cap, base_currency)}")
    if request.max_pe is not None:
        labels.append(f"P/E 0–{request.max_pe:g}")
    if request.min_roe_pct is not None:
        labels.append(f"ROE ≥ {request.min_roe_pct:g}%")
    if request.max_debt_to_equity is not None:
        labels.append(f"debt / equity ≤ {request.max_debt_to_equity:g}×")
    if request.min_revenue_growth_pct is not None:
        labels.append(f"revenue growth ≥ {request.min_revenue_growth_pct:g}%")
    if request.min_dividend_yield_pct is not None:
        labels.append(f"dividend yield ≥ {request.min_dividend_yield_pct:g}%")
    if request.min_drawdown_pct is not None:
        labels.append(f"≥ {request.min_drawdown_pct:g}% below 52-week high")
    return labels
