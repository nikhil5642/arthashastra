"""`screen` — stocks anywhere in the world that pass the asked filters.

The cache holds only what has been analysed; `screen` reads whole markets.
Three stages, cheapest first:

  1. Yahoo's screener (`data_sources.universe_source`): the ratio filters —
     P/E, ROE, debt / equity, revenue growth, dividend yield — run
     server-side over every listing. A narrow screen (one industry, tight
     filters) is read whole in one query spanning all its markets: a
     request or two. A broad one is read market by market, largest first,
     until `--scan` tradeable listings are in hand per market. Depositary
     receipts and foreign copies are skipped either way (`_tradeable`).
  2. Here: market cap in the base currency (live FX per listing), distance
     below the 52-week high, and one listing per company — a listing in
     the company's own reporting currency first, then the most traded, so
     2330.TW beats the TSM receipt and RELIANCE.NS beats RELIANCE.BO.
     Then the order: by default the rubric preview (`fundamental_engine.
     preview` — how many of the fundamentals rubric's marks each company
     clears), which never looks at size.
  3. `--score N`: the first N of that order are scored like `refresh` and
     ranked on the deterministic pillars (`fundamental_engine.scorecard`),
     each with the evidence behind its scores. They join the cache, so
     `pick --tickers`, `brief`, `compare` and `/analyse-stock` can use them.

No company is preferred for being large. Four listed companies in five
are small, and the best business in an industry is as likely to be one of
them: of the 113 chip makers with a 15%+ return on equity, five of the
six that scored best on fundamentals were not large caps. So nothing here
is truncated by market cap unless that is asked for (`--sort mcap`, or a
market-cap bound): a narrow screen reads every match, the preview orders
them blind to size, companies level on the preview take turns across the
size bands, and the result says how the sizes were covered. A screen too
broad to preview is spread evenly across sizes and says so; one too broad
even to read whole falls back to each market's largest listings, and says
that loudly.

Market-cap bounds are given in the base currency. Read market by market,
Yahoo filters amounts in each market's own currency, so a bound is
converted with the currency of the market's largest tradeable listings
(one probe request per market); read whole, the bound is left out of the
query. Either way every listing is checked again exactly.

A market Yahoo does not answer for is named in the result, never dropped
silently: a list that looks complete and isn't is worse than an error.

Sector and industry names are forgiving: Yahoo's exact name in any case
or punctuation ("software infrastructure"), else every name containing
the words ("bank" → both bank industries). What was matched is shown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_sources import universe_source
from data_sources.fundamentals_source import is_financial_label
from data_sources.fundamentals_source.valuation import MIN_PLAUSIBLE_PE
from data_sources.fx_source import conversion_rate, major_unit
from data_sources.names import clean_company_name
from data_sources.universe_source import PAGE_SIZE, Criteria, Listing, ScreenPage
from fundamental_engine.commands.compare import render_shortlist
from fundamental_engine.config import AnalystConfig
from fundamental_engine.engine import Engine
from fundamental_engine.preview import Preview, preview
from fundamental_engine.scorecard import (
    DEFAULT_REUSE_HOURS,
    RankBy,
    Scorecard,
    Shortlist,
    score_tickers,
)
from reports.formatting import format_money, format_price, make_console
from scorers.fundamentals_scorer.marks import INDUSTRIAL_MARKS, LENDER_MARKS

log = logging.getLogger(__name__)

DEFAULT_SCAN = PAGE_SIZE  # tradeable listings kept per market, largest first
MAX_PAGES_PER_MARKET = 4  # 1,000 raw listings: Brazil's and Germany's top pages are mostly receipts
# A screen matching at most this many listings is read whole, in one query
# for all its markets: 60 requests at most, no more than one per market would
# take — and every match of every size, not each market's largest.
WHOLE_READ_LIMIT = 15_000
# Up to this many listings the rubric preview is affordable (it asks about the
# whole screen two dozen times); beyond it the order is an even spread by size.
PREVIEW_LIMIT = 4_000
DEFAULT_LIMIT = 25  # rows shown
NEXT_IN_LINE_SHOWN = 10  # unscored companies listed in the JSON, best first
PROBE_SIZE = 100  # listings sampled to learn a market's currency
MIN_DAILY_TURNOVER = 1e-5  # 0.001% of market cap traded a day — see `_tradeable`
REQUEST_SPACING_SECONDS = 0.5
RETRY_PAUSE_SECONDS = 2.0
# Markets in a row Yahoo may fail to answer for before the rest are given up on.
MAX_UNANSWERED_IN_A_ROW = 3


class ScreenSort(StrEnum):
    QUALITY = "quality"  # the rubric preview, blind to size (the default)
    MARKET_CAP = "mcap"  # largest first — only when size is what was asked for
    DRAWDOWN = "drawdown"
    EARNINGS_YIELD = "earnings-yield"


class SizeBand(StrEnum):
    """Which third of a screen's companies, by market cap, one falls in."""

    LARGE = "large"
    MID = "mid"
    SMALL = "small"


class Order(StrEnum):
    """How a screen's companies ended up ordered."""

    PREVIEW = "preview"  # by the rubric preview
    SIZE_SPREAD = "size_spread"  # evenly across the size bands: too many to preview
    AS_SORTED = "as_sorted"  # by the --sort that was asked for


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
    sort: ScreenSort = ScreenSort.QUALITY
    limit: int = DEFAULT_LIMIT
    scan: int = DEFAULT_SCAN
    score: int = 0
    rank_by: RankBy = RankBy.RISK_ADJUSTED  # what the scored names are ordered by
    reuse_hours: float = DEFAULT_REUSE_HOURS  # cached rows this fresh are not fetched again

    def criteria(self, markets: tuple[str, ...]) -> Criteria:
        """The server-side filters for these markets (market-cap bounds added later)."""
        return Criteria(
            markets=markets,
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
    size_band: SizeBand = SizeBand.LARGE  # within this screen's companies
    scale_vs_largest_pct: float = 100.0  # market cap as a share of the screen's largest company
    preview: Preview | None = None  # rubric marks cleared; None when not previewed

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
            "size_band": self.size_band.value,
            "scale_vs_largest_pct": self.scale_vs_largest_pct,
            "preview": self.preview.score if self.preview else None,
            # Share of the rubric's screenable marks Yahoo has a value for.
            "preview_coverage": self.preview.coverage if self.preview else None,
            "drawdown_pct": listing.drawdown_pct,
            "trailing_pe": listing.trailing_pe,
            "traded_value_base": (
                round(self.traded_value_base) if self.traded_value_base is not None else None
            ),
        }


@dataclass(slots=True)
class ScreenResult:
    matches: list[Match]  # one per company, in `order`
    markets_read: int
    listings_read: int
    untradeable: int  # receipts, foreign copies, and listings that barely trade
    without_fx: int  # listings dropped: no FX quote for their currency
    unread_markets: list[str] = field(default_factory=list)  # Yahoo did not answer for these
    order: Order = Order.AS_SORTED
    # False when the screen was too broad to read whole: only each market's
    # largest listings were read, so smaller companies are missing from it.
    read_whole: bool = True

    def size_mix(self) -> dict[SizeBand, int]:
        return {band: sum(1 for m in self.matches if m.size_band is band) for band in SizeBand}


@dataclass(slots=True)
class _Reading:
    """What stage 1 brought back."""

    listings: list[tuple[str, Listing]] = field(default_factory=list)  # (market, tradeable listing)
    untradeable: int = 0
    unread_markets: list[str] = field(default_factory=list)


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
    reading = await _read_whole(markets, request)
    read_whole = reading is not None
    if reading is None:
        reading = await _read_market_by_market(markets, request, rates)
    listings_read = len(reading.listings) + reading.untradeable

    matches: list[Match] = []
    without_fx = 0
    for market, listing in reading.listings:
        rate = await rates.per_unit(major_unit(listing.currency)[0])
        if rate is None:
            without_fx += 1
            continue
        match = _match(listing, market, rate, request)
        if match is not None:
            matches.append(match)
    companies = _with_size_bands(_one_per_company(matches))

    order = Order.AS_SORTED
    if request.sort is ScreenSort.QUALITY and companies:
        order = Order.SIZE_SPREAD
        if read_whole and listings_read <= PREVIEW_LIMIT:
            previews = await _previews(request, markets, companies)
            if previews is not None:
                companies = [replace(m, preview=previews.get(m.listing.symbol)) for m in companies]
                order = Order.PREVIEW
    return ScreenResult(
        matches=_ordered(companies, request.sort, order),
        markets_read=len(markets) - len(reading.unread_markets),
        listings_read=listings_read,
        untradeable=reading.untradeable,
        without_fx=without_fx,
        unread_markets=reading.unread_markets,
        order=order,
        read_whole=read_whole,
    )


async def _read_whole(markets: tuple[str, ...], request: ScreenRequest) -> _Reading | None:
    """Every match, in one query spanning all the markets — None when that is too many.

    A narrow screen matches a few hundred listings worldwide: a request or
    two, where reading market by market takes one per market. Market-cap
    bounds stay out of the query (it can't state them in 59 currencies)
    and are checked exactly per listing afterwards.
    """
    criteria = request.criteria(markets)
    first = await _page(criteria)
    if first is None:
        # Yahoo is not answering at all: say so now rather than ask 59 more times.
        return _Reading(unread_markets=list(markets))
    if first.total > WHOLE_READ_LIMIT:
        return None
    pages = [first]
    for offset in range(PAGE_SIZE, first.total, PAGE_SIZE):
        await asyncio.sleep(REQUEST_SPACING_SECONDS)
        page = await _page(criteria, offset=offset)
        if page is None:
            return None  # market by market instead: that read names what it could not get
        pages.append(page)

    reading = _Reading()
    only_market = markets[0] if len(markets) == 1 else ""
    for listing in (listing for page in pages for listing in page.listings):
        if _tradeable(listing):
            market = universe_source.market_of(listing.exchange_code) or only_market
            reading.listings.append((market, listing))
        else:
            reading.untradeable += 1
    log.info(
        "screen: read whole — %d tradeable listings (%d skipped) in %d request(s)",
        len(reading.listings), reading.untradeable, len(pages),
    )
    return reading


async def _read_market_by_market(
    markets: tuple[str, ...], request: ScreenRequest, rates: _Rates
) -> _Reading:
    """Each market's largest tradeable listings — for a screen too broad to read whole."""
    reading = _Reading()
    unanswered_in_a_row = 0
    for i, market in enumerate(markets):
        if unanswered_in_a_row >= MAX_UNANSWERED_IN_A_ROW:
            reading.unread_markets.extend(markets[i:])
            break
        if i:
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
        read = await _read_market(market, request, rates)
        if read is None:
            reading.unread_markets.append(market)
            unanswered_in_a_row += 1
            continue
        unanswered_in_a_row = 0
        listings, skipped = read
        reading.listings.extend((market, listing) for listing in listings)
        reading.untradeable += skipped
    return reading


async def _read_market(
    market: str, request: ScreenRequest, rates: _Rates
) -> tuple[list[Listing], int] | None:
    """Up to `request.scan` tradeable listings, largest first, and how many were skipped.

    None when Yahoo did not answer for this market.
    """
    criteria = request.criteria((market,))
    if request.bounds_market_cap:
        probe = await _page(criteria, size=PROBE_SIZE)
        if probe is None:
            return None
        bounded = await _with_local_bounds(criteria, probe, request, rates)
        if bounded is None:
            return [], 0
        await asyncio.sleep(REQUEST_SPACING_SECONDS)
        criteria = bounded

    kept: list[Listing] = []
    skipped = 0
    for page_number in range(MAX_PAGES_PER_MARKET):
        if page_number:
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
        page = await _page(criteria, offset=page_number * PAGE_SIZE)
        if page is None:
            return None
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


async def _page(
    criteria: Criteria, *, offset: int = 0, size: int = PAGE_SIZE
) -> ScreenPage | None:
    """One screener page; a request Yahoo does not answer is tried once more."""
    page = await universe_source.fetch_page(criteria, offset=offset, size=size)
    if page is None:
        await asyncio.sleep(RETRY_PAUSE_SECONDS)
        page = await universe_source.fetch_page(criteria, offset=offset, size=size)
    return page


def _tradeable(listing: Listing) -> bool:
    """A real listing of the company: it trades, and its quote describes the company.

    Two kinds of depositary receipt and foreign copy crowd the top of
    their markets, and each fails one test:

      - Whole-company market cap, almost no trading — Brazil's BDRs,
        Frankfurt and Zurich copies of US stocks (236 of Brazil's 246
        largest "listings" are BDRs). They trade 0.0000001%–0.0001% of
        their market cap a day; primary listings trade 0.001% or more
        (even Saudi Aramco, with a 2% free float, trades 0.003%).
      - Real trading, a market cap that is only the receipt programme's —
        Argentine CEDEARs, Singapore SDRs, fractional Canadian receipts.
        Their price and earnings sit on different share bases, which shows
        as an impossible P/E (`_receipt_quote`).
    """
    turnover = listing.daily_turnover
    if turnover is None or turnover < MIN_DAILY_TURNOVER:
        return False
    return not _receipt_quote(listing)


def _receipt_quote(listing: Listing) -> bool:
    """Priced below 1.5× annual earnings, in a currency the company doesn't report in.

    No profitable company trades there; a quote that says so is dividing
    one instrument's price by another's earnings. Same-currency listings
    are left alone — a tiny P/E there is a real (one-off-inflated) number.
    """
    return (
        listing.cross_currency
        and listing.trailing_pe is not None
        and listing.trailing_pe < MIN_PLAUSIBLE_PE
    )


async def _with_local_bounds(
    criteria: Criteria, probe: ScreenPage, request: ScreenRequest, rates: _Rates
) -> Criteria | None:
    """Base-currency market-cap bounds → the market's own currency; None skips the market.

    `probe` is a sample of the market's largest listings, read without bounds.
    """
    # London's largest "listings" include euro-quoted copies of US stocks,
    # so the market's currency comes from the listings that really trade.
    sample = [listing for listing in probe.listings if _tradeable(listing)] or probe.listings
    if not sample:
        return None
    currency = Counter(major_unit(listing.currency)[0] for listing in sample).most_common(1)[0][0]
    rate = await rates.per_unit(currency)
    if rate is None:
        log.warning("screen: %s skipped — no FX rate for %s", criteria.markets, currency)
        return None
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
    """Keep one listing per company: its home-currency listing, then the most traded.

    A listing that trades in the company's reporting currency is the one
    whose statements, per-share figures and price all line up, so it beats
    a receipt even when the receipt trades more (2330.TW over TSM). Among
    equals the most traded wins: NSE over BSE, Nasdaq over Frankfurt.
    """
    def preference(match: Match) -> tuple[bool, float]:
        return (not match.listing.cross_currency, match.traded_value_base or 0.0)

    best: dict[str, Match] = {}
    for match in matches:
        key = clean_company_name(match.listing.name).lower() or match.listing.symbol
        current = best.get(key)
        if current is None or preference(match) > preference(current):
            best[key] = match
    return list(best.values())


def _with_size_bands(matches: list[Match]) -> list[Match]:
    """Each company marked with the third of this screen it falls in, by market cap."""
    largest_first = sorted(matches, key=lambda m: m.market_cap_base, reverse=True)
    bands = tuple(SizeBand)
    largest = largest_first[0].market_cap_base if largest_first else 0.0
    return [
        replace(
            match,
            size_band=bands[i * len(bands) // len(largest_first)],
            scale_vs_largest_pct=round(match.market_cap_base / largest * 100, 3) if largest else 100.0,
        )
        for i, match in enumerate(largest_first)
    ]


async def _previews(
    request: ScreenRequest, markets: tuple[str, ...], companies: list[Match]
) -> dict[str, Preview] | None:
    """Every company's rubric preview; None when Yahoo did not answer in full.

    Lenders are previewed on the bank rubric's marks, everything else on
    the industrial rubric's.
    """
    criteria = request.criteria(markets)
    symbols = {match.listing.symbol for match in companies}
    lenders = await _lenders(request, criteria, symbols)
    if lenders is None:
        return None
    previews: dict[str, Preview] = {}
    for marks, group in ((INDUSTRIAL_MARKS, symbols - lenders), (LENDER_MARKS, lenders)):
        if not group:
            continue
        scores = await preview(criteria, group, marks)
        if scores is None:
            return None
        previews.update(scores)
    return previews


async def _lenders(
    request: ScreenRequest, criteria: Criteria, symbols: set[str]
) -> set[str] | None:
    """The screen's financial companies. Asks Yahoo only when the request doesn't say."""
    asked_for = request.industries or request.sectors
    if asked_for and all(is_financial_label(name) for name in asked_for):
        return set(symbols)
    if asked_for and not any(is_financial_label(name) for name in asked_for):
        return set()
    financial_sectors = tuple(s for s in universe_source.sectors() if is_financial_label(s))
    if not financial_sectors:
        return set()
    found = await universe_source.fetch_symbols(replace(criteria, sectors=financial_sectors))
    return None if found is None else found & symbols


def _ordered(matches: list[Match], sort: ScreenSort, order: Order) -> list[Match]:
    """The companies in the order they are shown and scored."""
    if order is Order.PREVIEW:
        return _by_preview(matches)
    if order is Order.SIZE_SPREAD:
        return _taking_turns_by_size(matches)

    def value(match: Match) -> float | None:
        if sort is ScreenSort.DRAWDOWN:
            return match.listing.drawdown_pct
        if sort is ScreenSort.EARNINGS_YIELD:
            return match.earnings_yield_pct
        return match.market_cap_base

    # Best first; a listing missing the sort value goes last.
    return sorted(matches, key=lambda m: (value(m) is not None, value(m) or 0.0), reverse=True)


def _by_preview(matches: list[Match]) -> list[Match]:
    """Highest preview first. Companies level on it take turns across the size bands.

    A thin preview — one resting on a few known ratios — can't be compared
    with a well-covered one, so those follow, in the same order; companies
    Yahoo knows nothing about come last.
    """
    ordered: list[Match] = []
    for thin in (False, True):
        group = [m for m in matches if m.preview is not None and m.preview.thin is thin]
        for level in sorted({m.preview.score for m in group if m.preview}, reverse=True):
            ordered.extend(
                _taking_turns_by_size([m for m in group if m.preview and m.preview.score == level])
            )
    return ordered + _taking_turns_by_size([m for m in matches if m.preview is None])


def _taking_turns_by_size(matches: list[Match]) -> list[Match]:
    """One large, one mid, one small, and round again — no size ahead of another."""
    queues = [
        sorted((m for m in matches if m.size_band is band), key=lambda m: -m.market_cap_base)
        for band in SizeBand
    ]
    ordered: list[Match] = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


# ── Command (stage 3 is `scorecard.score_tickers`) ───────────────────────────


def run_screen_command(request: ScreenRequest, *, as_json: bool = False) -> int:
    try:
        request = resolve_names(request)
    except ValueError as problem:
        print(f"[screen] {problem}")
        return 2

    cfg = AnalystConfig.from_env()
    result = asyncio.run(screen(request, base_currency=cfg.base_currency))
    shortlist = Shortlist(ranked_by=request.rank_by)
    if request.score > 0 and result.matches:
        shortlist = asyncio.run(
            score_tickers(
                [match.listing.symbol for match in result.matches[: request.score]],
                Engine(cfg),
                cfg.data_cache_dir,
                rank_by=request.rank_by,
                reuse_hours=request.reuse_hours,
            )
        )

    shortlist.cards = _with_screen_context(shortlist.cards, result.matches)

    if as_json:
        print(json.dumps(_payload(result, shortlist, request, cfg.base_currency), indent=2))
        return 0
    _render(result, shortlist, request, base_currency=cfg.base_currency, console=make_console())
    return 0


def resolve_names(request: ScreenRequest) -> ScreenRequest:
    """The request with markets, sectors and industries in Yahoo's own spelling.

    Raises ValueError naming whatever matches nothing (and the valid
    values), or an out-of-range count.
    """
    if min(request.scan, request.limit) < 1 or request.score < 0:
        raise ValueError("--scan and --limit must be at least 1, --score at least 0.")
    return replace(
        request,
        markets=_yahoo_names("market", request.markets, universe_source.markets, whole_only=True),
        sectors=_yahoo_names("sector", request.sectors, universe_source.sectors),
        industries=_yahoo_names("industry", request.industries, universe_source.industries),
    )


def _yahoo_names(
    label: str, given: Sequence[str], valid: Callable[[], list[str]], *, whole_only: bool = False
) -> tuple[str, ...]:
    """Yahoo's names for what was typed: the name itself, else every name containing it.

    Case and punctuation are ignored ("software infrastructure" is
    "Software—Infrastructure"). Market codes must match whole: "in" is
    India, not every code with those letters.
    """
    if not given:
        return ()
    accepted = valid()
    names: list[str] = []
    unknown: list[str] = []
    for term in given:
        wanted = _plain(term)
        matched = [name for name in accepted if _plain(name) == wanted]
        if not matched and wanted and not whole_only:
            matched = [name for name in accepted if wanted in _plain(name)]
        names.extend(matched)
        if not matched:
            unknown.append(term)
    if unknown:
        raise ValueError(f"Unknown {label}(s): {', '.join(unknown)}. Valid: {', '.join(accepted)}")
    return tuple(dict.fromkeys(names))


def _plain(text: str) -> str:
    """Lower-case words only: 'Software—Infrastructure' → 'software infrastructure'."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _with_screen_context(cards: list[Scorecard], matches: list[Match]) -> list[Scorecard]:
    """Each scored company marked with its size band and preview in this screen."""
    found = {match.listing.symbol: match for match in matches}
    return [
        replace(card, size_band=match.size_band.value,
                preview=match.preview.score if match.preview else None,
                scale_vs_largest_pct=match.scale_vs_largest_pct)
        if (match := found.get(card.ticker)) is not None
        else card
        for card in cards
    ]


def _payload(
    result: ScreenResult, shortlist: Shortlist, request: ScreenRequest, base: str
) -> dict[str, object]:
    scored = {card.ticker for card in shortlist.cards}
    return {
        "base_currency": base,
        "filters": _filter_labels(request, base),
        "markets_read": result.markets_read,
        "unread_markets": result.unread_markets,
        "listings_read": result.listings_read,
        # False: too broad to read whole — only each market's largest listings were read.
        "read_whole": result.read_whole,
        "companies_matched": len(result.matches),
        "size_mix": {band.value: count for band, count in result.size_mix().items()},
        "ordered_by": result.order.value,
        "untradeable": result.untradeable,
        "without_fx": result.without_fx,
        "matches": [match.to_dict() for match in result.matches[: request.limit]],
        **shortlist.to_dict(),
        "scored_size_mix": {
            band.value: sum(1 for card in shortlist.cards if card.size_band == band.value)
            for band in SizeBand
        },
        # The best of what was not scored, in order: where --score N+ would go next.
        "next_in_line": [
            match.to_dict() for match in result.matches if match.listing.symbol not in scored
        ][:NEXT_IN_LINE_SHOWN] if shortlist.cards else [],
    }


def _render(
    result: ScreenResult,
    shortlist: Shortlist,
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
    if result.unread_markets:
        lines.append(
            f"[bold red]Yahoo's screener did not answer for: "
            f"{', '.join(result.unread_markets)} — this list is incomplete. Run it again."
            f"[/bold red]"
        )
    if not result.read_whole:
        lines.append(
            f"[bold red]Too broad to read whole: only each market's largest {request.scan} "
            f"listings were read, so smaller companies are missing. Add a sector, an "
            f"industry or a ratio filter to cover every size.[/bold red]"
        )
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
    if result.matches:
        lines.append(_size_line(result, base_currency))
        lines.append(_ORDER_NOTES[result.order].format(listings=result.listings_read))
    console.print(Panel("\n".join(line for line in lines if line), title="Global screen",
                        title_align="left"))

    if not result.matches:
        console.print(
            "[yellow]Nothing matched. Loosen a filter, add markets, or raise --scan "
            "to read deeper into each market.[/yellow]"
        )
        return

    shown = min(request.limit, len(result.matches))
    table = Table(title=f"{shown} of {len(result.matches):,} companies — {_order_label(result, request)}")
    for column, justify in (
        ("#", "right"), ("Ticker", "left"), ("Company", "left"), ("Exchange", "left"),
        ("Size", "left"), ("Preview", "right"), ("Price", "right"), ("Market cap", "right"),
        ("Below 52w high", "right"), ("P/E", "right"),
    ):
        table.add_column(column, justify=justify)
    for rank, match in enumerate(result.matches[: request.limit], start=1):
        listing = match.listing
        table.add_row(
            str(rank),
            f"[bold]{listing.symbol}[/bold]",
            listing.name[:34],
            listing.exchange,
            match.size_band.value,
            _preview_cell(match.preview),
            format_price(listing.price, listing.currency),
            format_money(match.market_cap_base, base_currency),
            f"{listing.drawdown_pct:.0f}%" if listing.drawdown_pct is not None else "—",
            f"{listing.trailing_pe:.1f}" if listing.trailing_pe is not None else "—",
        )
    console.print(table)

    if shortlist.cards or shortlist.not_scored:
        render_shortlist(console, shortlist, base_currency=base_currency)
    if shortlist.cards:
        unscored = len(result.matches) - len(shortlist.cards) - len(shortlist.not_scored)
        if unscored > 0:
            console.print(
                f"[dim]{unscored:,} more companies matched and were not scored — "
                f"--score {len(result.matches)} scores them all.[/dim]"
            )
        tickers = " ".join(card.ticker for card in shortlist.cards)
        console.print(f"Pick from them: pick --amount <BUDGET> --tickers {tickers}")


_ORDER_NOTES: dict[Order, str] = {
    Order.PREVIEW: (
        "[dim]Ordered by rubric preview: the share of the fundamentals rubric's marks a "
        "company clears on Yahoo's trailing numbers. Size plays no part.[/dim]"
    ),
    Order.SIZE_SPREAD: (
        "[yellow]Too many listings to preview ({listings:,}): shown as an even spread "
        "across sizes, not a quality order. Narrow by industry or add a filter.[/yellow]"
    ),
    Order.AS_SORTED: "",
}


def _preview_cell(preview: Preview | None) -> str:
    """'94', or '100 (thin)' for a score resting on too few known ratios."""
    if preview is None:
        return "—"
    return f"{preview.score:.0f}" + (" [dim](thin)[/dim]" if preview.thin else "")


def _order_label(result: ScreenResult, request: ScreenRequest) -> str:
    if result.order is Order.PREVIEW:
        return "best rubric preview first"
    if result.order is Order.SIZE_SPREAD:
        return "an even spread across sizes"
    return f"by {request.sort}"


def _size_line(result: ScreenResult, base_currency: str) -> str:
    """'By size: 38 large · 38 mid (under ₹74,000 Cr) · 37 small (under ₹6,000 Cr)'."""
    parts: list[str] = []
    for band, count in result.size_mix().items():
        if not count:
            continue
        largest = max(m.market_cap_base for m in result.matches if m.size_band is band)
        ceiling = "" if band is SizeBand.LARGE else f" (up to {format_money(largest, base_currency)})"
        parts.append(f"{count} {band.value}{ceiling}")
    return "By size: " + " · ".join(parts) + " — thirds of the companies matched"


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
