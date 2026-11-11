"""Global universe — every listed equity Yahoo's screener covers.

`fundamentals_source` reads one company deeply; this module reads whole
markets shallowly. Yahoo's equity screener covers ~80,000 listings in 59
markets (18,000 in the US, 7,300 in India, 3,800 in Japan, …). Yahoo
applies the filters server-side to every listing in a market and returns
up to 250 of the largest matches per request, so a screen reads entire
markets without fetching a single company's statements.

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


@dataclass(frozen=True, slots=True)
class Criteria:
    """One market's server-side filters. Market-cap bounds are in its own currency."""

    market: str
    sectors: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    max_pe: float | None = None
    min_roe_pct: float | None = None
    max_debt_to_equity: float | None = None  # ×: 0.5 = half as much debt as equity
    min_revenue_growth_pct: float | None = None
    min_dividend_yield_pct: float | None = None
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

    @property
    def cross_currency(self) -> bool:
        """Trades in one currency, reports in another (an ADR, a Hong Kong H-share, a CEDEAR)."""
        return bool(self.financial_currency) and (
            major_unit(self.currency)[0] != self.financial_currency.upper()
        )

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
            currency=_text(quote, "currency"),
            price=price,
            market_cap=market_cap,
            high_52w=_positive(quote.get("fiftyTwoWeekHigh")),
            trailing_pe=_positive(quote.get("trailingPE")),
            avg_daily_volume=_positive(quote.get("averageDailyVolume3Month")),
            financial_currency=_text(quote, "financialCurrency"),
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


def build_query(criteria: Criteria) -> EquityQuery:
    """The Yahoo query for one market. Raises ValueError on an unknown value."""
    from yfinance import EquityQuery as Q

    conditions = [
        Q("eq", ["region", criteria.market]),
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
    return Q("and", conditions)


async def fetch_page(
    criteria: Criteria, *, offset: int = 0, size: int = PAGE_SIZE
) -> ScreenPage:
    """The largest listings in one market that pass `criteria`, biggest first."""
    query = build_query(criteria)
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, _fetch_screen_sync, query, offset, size)
    return _parse_page(raw)


def _fetch_screen_sync(query: EquityQuery, offset: int, size: int) -> dict[str, object]:
    """One request to Yahoo's screener, largest market cap first. Tests monkeypatch this."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — screening disabled")
        return {}
    try:
        response = yf.screen(
            query, offset=offset, size=size, sortField=_MARKET_CAP, sortAsc=False
        )
    except Exception as e:
        log.warning("Yahoo screener request failed: %s", e)
        return {}
    return response if isinstance(response, dict) else {}


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
