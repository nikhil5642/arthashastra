"""Global universe — every listed equity Yahoo's screener covers.

`fundamentals_source` reads one company deeply; this module reads whole
markets shallowly. Yahoo's equity screener covers ~80,000 listings in 59
markets (18,000 in the US, 7,300 in India, 3,800 in Japan, …). Yahoo
applies the filters server-side to every listing in a market and returns
up to 250 of the largest matches per request, so a screen reads entire
markets without fetching a single company's statements.

One query can span several markets — all 59 if asked — and returns exactly
the listings the same query would return market by market (verified live:
250 of 250 for one industry worldwide). A narrow screen is therefore one
or two requests, not one per market.

Yahoo filters on many more ratios than it returns. `Bound` puts one of
them (`Measure`) into a query, and `fetch_symbols` answers "which of this
screen's listings clear it?" — a set-membership question that costs a
request, not a company's statements. Enough of those questions make a
picture of every listing in a screen, whatever its size.

Two Yahoo conventions shape the query (verified live, 2026-11):

  - Ratios are currency-free and in percent: ROE 15 means 15%,
    debt / equity 50 means 0.5×, dividend yield 3 means 3%. Loss-makers
    carry a negative P/E, so "P/E at most 20" is the range 0–20 — a loss
    is never cheap.
  - Amounts are in each listing's own currency: an NSE market cap is in
    rupees, a Nasdaq one in dollars. `Criteria` therefore takes market-cap
    bounds in the market's own currency; the caller converts.

Markets, sectors and industries are yfinance's own lists — no market
table lives here.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING

from data_sources.fx_source import major_unit

if TYPE_CHECKING:
    from yfinance import EquityQuery

log = logging.getLogger(__name__)

PAGE_SIZE = 250  # Yahoo's maximum per request

_MARKET_CAP = "intradaymarketcap"
_PE = "peratio.lasttwelvemonths"
_ROE = "returnonequity.lasttwelvemonths"
_DEBT_TO_EQUITY = "totaldebtequity.lasttwelvemonths"
_REVENUE_GROWTH = "totalrevenues1yrgrowth.lasttwelvemonths"
_DIVIDEND_YIELD = "forward_dividend_yield"


class Measure(StrEnum):
    """A trailing-twelve-month ratio Yahoo's screener can be asked about."""

    RETURN_ON_EQUITY = "return_on_equity"  # %
    RETURN_ON_CAPITAL = "return_on_capital"  # %
    RETURN_ON_ASSETS = "return_on_assets"  # %
    DEBT_TO_EQUITY = "debt_to_equity"  # ×: 0.5 = half as much debt as equity
    CURRENT_RATIO = "current_ratio"  # ×
    QUICK_RATIO = "quick_ratio"  # ×
    REVENUE_GROWTH = "revenue_growth"  # %, one year
    PROFIT_GROWTH = "profit_growth"  # %, net income, one year


# Measure → (Yahoo's field, what one of our units is in Yahoo's).
_MEASURE_FIELDS: dict[Measure, tuple[str, float]] = {
    Measure.RETURN_ON_EQUITY: (_ROE, 1.0),
    Measure.RETURN_ON_CAPITAL: ("returnontotalcapital.lasttwelvemonths", 1.0),
    Measure.RETURN_ON_ASSETS: ("returnonassets.lasttwelvemonths", 1.0),
    Measure.DEBT_TO_EQUITY: (_DEBT_TO_EQUITY, 100.0),  # Yahoo states it in percent
    Measure.CURRENT_RATIO: ("currentratio.lasttwelvemonths", 1.0),
    Measure.QUICK_RATIO: ("quickratio.lasttwelvemonths", 1.0),
    Measure.REVENUE_GROWTH: (_REVENUE_GROWTH, 1.0),
    Measure.PROFIT_GROWTH: ("netincome1yrgrowth.lasttwelvemonths", 1.0),
}
# Below any ratio a company can report: "at least this" selects every
# listing Yahoo has a value for, which is how an unknown is told from a fail.
_ANY_KNOWN_VALUE = -1e12


@dataclass(frozen=True, slots=True)
class Bound:
    """One measure held to a floor or a ceiling — or, with neither, merely known to Yahoo."""

    measure: Measure
    at_least: float | None = None
    at_most: float | None = None


@dataclass(frozen=True, slots=True)
class Criteria:
    """The server-side filters, for one market or several.

    Market-cap bounds are in a market's own currency, so they only mean
    something when `markets` holds one market.
    """

    markets: tuple[str, ...]
    sectors: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    max_pe: float | None = None
    min_roe_pct: float | None = None
    max_debt_to_equity: float | None = None  # ×: 0.5 = half as much debt as equity
    min_revenue_growth_pct: float | None = None
    min_dividend_yield_pct: float | None = None
    bounds: tuple[Bound, ...] = ()  # further conditions, one per measure asked about
    min_market_cap: float | None = None
    max_market_cap: float | None = None


@dataclass(frozen=True, slots=True)
class Listing:
    """One screener row: a listing's quote, not its statements."""

    symbol: str  # Yahoo symbol, the project's ticker ID
    name: str
    exchange: str  # Yahoo's display name, e.g. "NasdaqGS", "NSE"
    currency: str  # trading currency, possibly a minor unit ("GBp")
    price: float
    market_cap: float  # in the trading currency's major unit (pounds for GBp)
    high_52w: float | None = None
    trailing_pe: float | None = None
    avg_daily_volume: float | None = None  # shares, 3-month average
    financial_currency: str = ""  # the company's reporting currency; "" when Yahoo omits it
    exchange_code: str = ""  # Yahoo's exchange code ("NMS", "TAI") — see `market_of`
    eps_ttm: float | None = None  # trailing-twelve-month earnings per share
    book_value_per_share: float | None = None

    @property
    def cross_currency(self) -> bool:
        """Trades in one currency, reports in another (an ADR, a Hong Kong H-share, a CEDEAR)."""
        return bool(self.financial_currency) and (
            major_unit(self.currency)[0] != self.financial_currency.upper()
        )

    @property
    def implied_roe_pct(self) -> float | None:
        """Earnings per share ÷ book value per share: return on equity, from the quote alone.

        None for a loss, for negative book value, and for a listing that
        trades in another currency than it reports in (Yahoo quotes its
        two per-share figures in different currencies).
        """
        if self.cross_currency or not self.eps_ttm or not self.book_value_per_share:
            return None
        return round(self.eps_ttm / self.book_value_per_share * 100, 1)

    @property
    def drawdown_pct(self) -> float | None:
        """How far the price sits below its 52-week high, in percent."""
        if self.high_52w is None:
            return None
        return round(max(0.0, (1 - self.price / self.high_52w) * 100), 1)

    @property
    def daily_turnover(self) -> float | None:
        """An average day's trading as a fraction of market cap (currency-free)."""
        if self.avg_daily_volume is None:
            return None
        divisor = major_unit(self.currency)[1]  # pence → pounds, fils → dinars
        return self.price / divisor * self.avg_daily_volume / self.market_cap

    @classmethod
    def from_quote(cls, quote: dict[str, object]) -> Listing | None:
        """Narrow one raw Yahoo quote; None when it can't be priced and sized."""
        symbol = quote.get("symbol")
        price = _positive(quote.get("regularMarketPrice"))
        market_cap = _positive(quote.get("marketCap"))
        if not isinstance(symbol, str) or not symbol or price is None or market_cap is None:
            return None
        return cls(
            symbol=symbol.upper(),
            name=_text(quote, "longName", "shortName") or symbol.upper(),
            exchange=_text(quote, "fullExchangeName", "exchange"),
            exchange_code=_text(quote, "exchange"),
            currency=_text(quote, "currency"),
            price=price,
            market_cap=market_cap,
            high_52w=_positive(quote.get("fiftyTwoWeekHigh")),
            trailing_pe=_positive(quote.get("trailingPE")),
            avg_daily_volume=_positive(quote.get("averageDailyVolume3Month")),
            financial_currency=_text(quote, "financialCurrency"),
            eps_ttm=_positive(quote.get("epsTrailingTwelveMonths")),
            book_value_per_share=_positive(quote.get("bookValue")),
        )


@dataclass(frozen=True, slots=True)
class ScreenPage:
    total: int  # listings in the market that pass the filters
    listings: tuple[Listing, ...]


def markets() -> list[str]:
    """Every market code Yahoo's screener accepts: 'us', 'in', 'jp', 'gb', …"""
    return _valid_values("region")


def sectors() -> list[str]:
    """Yahoo's 11 sectors: 'Technology', 'Financial Services', …"""
    return _valid_values("sector")


def industries() -> list[str]:
    """Every industry Yahoo's screener accepts, across all sectors."""
    return _valid_values("industry", nested=True)


def market_of(exchange_code: str) -> str:
    """The market an exchange belongs to ('NMS' → 'us'), '' when Yahoo lists it nowhere."""
    return _markets_by_exchange().get(exchange_code, "")


@lru_cache(maxsize=1)
def _markets_by_exchange() -> dict[str, str]:
    """yfinance's own market → exchanges table, inverted. No exchange sits in two markets."""
    from yfinance import EquityQuery

    exchanges = EquityQuery("gt", [_MARKET_CAP, 0]).valid_values.get("exchange", {})
    if not isinstance(exchanges, dict):
        return {}
    return {code: market for market, codes in exchanges.items() for code in codes}


def build_query(criteria: Criteria) -> EquityQuery:
    """The Yahoo query for the criteria's markets. Raises ValueError on an unknown value."""
    from yfinance import EquityQuery as Q

    if not criteria.markets:
        raise ValueError("a screen needs at least one market")
    conditions = [
        _one_of("region", criteria.markets),
        Q("gte", [_MARKET_CAP, criteria.min_market_cap or 0]),
    ]
    if criteria.max_market_cap is not None:
        conditions.append(Q("lte", [_MARKET_CAP, criteria.max_market_cap]))
    if criteria.sectors:
        conditions.append(_one_of("sector", criteria.sectors))
    if criteria.industries:
        conditions.append(_one_of("industry", criteria.industries))
    if criteria.max_pe is not None:
        conditions.append(Q("btwn", [_PE, 0, criteria.max_pe]))
    if criteria.min_roe_pct is not None:
        conditions.append(Q("gte", [_ROE, criteria.min_roe_pct]))
    if criteria.max_debt_to_equity is not None:
        # Negative D/E means negative equity — never "low debt".
        conditions.append(Q("btwn", [_DEBT_TO_EQUITY, 0, criteria.max_debt_to_equity * 100]))
    if criteria.min_revenue_growth_pct is not None:
        conditions.append(Q("gte", [_REVENUE_GROWTH, criteria.min_revenue_growth_pct]))
    if criteria.min_dividend_yield_pct is not None:
        conditions.append(Q("gte", [_DIVIDEND_YIELD, criteria.min_dividend_yield_pct]))
    conditions.extend(_bound_condition(bound) for bound in criteria.bounds)
    return Q("and", conditions)


def _bound_condition(bound: Bound) -> EquityQuery:
    """One `Bound` as a Yahoo condition, in Yahoo's units."""
    from yfinance import EquityQuery as Q

    field, scale = _MEASURE_FIELDS[bound.measure]
    if bound.at_most is not None:
        # A ceiling is read from zero up, like `max_debt_to_equity`: below zero
        # is a different animal (negative equity), not "even lower".
        return Q("btwn", [field, (bound.at_least or 0.0) * scale, bound.at_most * scale])
    floor = bound.at_least * scale if bound.at_least is not None else _ANY_KNOWN_VALUE
    return Q("gte", [field, floor])


async def fetch_page(
    criteria: Criteria, *, offset: int = 0, size: int = PAGE_SIZE
) -> ScreenPage | None:
    """The listings that pass `criteria`, largest market cap first.

    None when Yahoo did not answer. That is not an empty page: a screen
    that silently drops a market it could not read gives a wrong answer
    that looks complete.
    """
    query = build_query(criteria)
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, _fetch_screen_sync, query, offset, size)
    return _parse_page(raw) if raw is not None else None


async def fetch_symbols(criteria: Criteria) -> set[str] | None:
    """The symbol of every listing that passes `criteria`; None when Yahoo did not answer."""
    symbols: set[str] = set()
    offset = 0
    while True:
        page = await fetch_page(criteria, offset=offset)
        if page is None:
            return None
        symbols.update(listing.symbol for listing in page.listings)
        offset += PAGE_SIZE
        if offset >= page.total or not page.listings:
            return symbols


def _fetch_screen_sync(query: EquityQuery, offset: int, size: int) -> dict[str, object] | None:
    """One request to Yahoo's screener; None when it fails. Tests monkeypatch this."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — screening disabled")
        return None
    try:
        response = yf.screen(
            query, offset=offset, size=size, sortField=_MARKET_CAP, sortAsc=False
        )
    except Exception as e:
        log.warning("Yahoo screener request failed: %s", e)
        return None
    return response if isinstance(response, dict) else None


def _parse_page(raw: dict[str, object]) -> ScreenPage:
    quotes = raw.get("quotes")
    listings = tuple(
        listing
        for quote in (quotes if isinstance(quotes, list) else [])
        if isinstance(quote, dict) and (listing := Listing.from_quote(quote)) is not None
    )
    total = raw.get("total")
    return ScreenPage(
        total=total if isinstance(total, int) else len(listings), listings=listings
    )


def _one_of(field: str, values: tuple[str, ...]) -> EquityQuery:
    from yfinance import EquityQuery as Q

    if len(values) == 1:
        return Q("eq", [field, values[0]])
    return Q("is-in", [field, *values])


def _valid_values(key: str, *, nested: bool = False) -> list[str]:
    """yfinance's accepted values for a screener field (`nested`: a dict of sets)."""
    from yfinance import EquityQuery

    values = EquityQuery("gt", [_MARKET_CAP, 0]).valid_values.get(key, ())
    if nested and isinstance(values, dict):
        return sorted({v for group in values.values() for v in group})
    return sorted(values)


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) and v > 0 else None


def _text(quote: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = quote.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
