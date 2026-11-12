"""Universe-source tests — the Yahoo screener request is mocked; queries are real."""

from __future__ import annotations

import pytest

from data_sources import universe_source
from data_sources.universe_source import Criteria, Listing, build_query, fetch_page


def _conditions(criteria: Criteria) -> set[tuple[object, ...]]:
    """Flatten the query into (OPERATOR, field, *values); an OR of EQs stays one IS-IN."""
    out: set[tuple[object, ...]] = set()
    for operand in build_query(criteria).to_dict()["operands"]:
        if operand["operator"] == "OR":
            values = tuple(sorted(o["operands"][1] for o in operand["operands"]))
            out.add(("IS-IN", operand["operands"][0]["operands"][0], *values))
        else:
            out.add((operand["operator"], *operand["operands"]))
    return out


def _quote(**overrides: object) -> dict[str, object]:
    quote: dict[str, object] = {
        "symbol": "tcs.ns", "longName": "Tata Consultancy Services Limited",
        "fullExchangeName": "NSE", "currency": "INR", "regularMarketPrice": 2475.8,
        "marketCap": 8.96e12, "fiftyTwoWeekHigh": 3350.0, "trailingPE": 17.36,
        "averageDailyVolume3Month": 3_100_000,
    }
    quote.update(overrides)
    return quote


def test_query_maps_every_filter_to_yahoos_fields_and_units():
    conditions = _conditions(
        Criteria(
            markets=("in",), sectors=("Technology", "Healthcare"), max_pe=20, min_roe_pct=15,
            max_debt_to_equity=0.5, min_revenue_growth_pct=10, min_dividend_yield_pct=2,
            min_market_cap=1e11, max_market_cap=1e12,
        )
    )
    assert conditions == {
        ("EQ", "region", "in"),
        ("GTE", "intradaymarketcap", 1e11),
        ("LTE", "intradaymarketcap", 1e12),
        ("IS-IN", "sector", "Healthcare", "Technology"),
        ("BTWN", "peratio.lasttwelvemonths", 0, 20),  # a loss is never cheap
        ("GTE", "returnonequity.lasttwelvemonths", 15),  # percent
        ("BTWN", "totaldebtequity.lasttwelvemonths", 0, 50.0),  # 0.5× → 50%
        ("GTE", "totalrevenues1yrgrowth.lasttwelvemonths", 10),
        ("GTE", "forward_dividend_yield", 2),
    }


def test_bare_query_is_one_market_with_a_market_cap():
    assert _conditions(Criteria(markets=("jp",))) == {
        ("EQ", "region", "jp"),
        ("GTE", "intradaymarketcap", 0),
    }


def test_one_query_can_span_several_markets():
    assert _conditions(Criteria(markets=("us", "tw", "kr"), industries=("Semiconductors",))) == {
        ("IS-IN", "region", "kr", "tw", "us"),
        ("GTE", "intradaymarketcap", 0),
        ("EQ", "industry", "Semiconductors"),
    }
    with pytest.raises(ValueError):
        build_query(Criteria(markets=()))


def test_an_exchange_code_names_its_market():
    assert universe_source.market_of("NMS") == "us"
    assert universe_source.market_of("TAI") == "tw"
    assert universe_source.market_of("NSI") == "in"
    assert universe_source.market_of("") == "" and universe_source.market_of("XXX") == ""


def test_values_outside_yahoos_lists_are_rejected():
    with pytest.raises(ValueError):
        build_query(Criteria(markets=("atlantis",)))
    assert "us" in universe_source.markets() and "in" in universe_source.markets()
    assert "Technology" in universe_source.sectors()
    assert "Semiconductors" in universe_source.industries()


async def test_page_keeps_listings_that_can_be_priced_and_sized(monkeypatch):
    seen: list[tuple[int, int]] = []

    def fake_screen(query, offset: int, size: int) -> dict[str, object]:
        seen.append((offset, size))
        return {
            "total": 812,
            "quotes": [
                _quote(),
                _quote(symbol="NOCAP.NS", marketCap=None),
                _quote(symbol="FREE.NS", regularMarketPrice=0),
                "not a quote",
            ],
        }

    monkeypatch.setattr(universe_source, "_fetch_screen_sync", fake_screen)
    page = await fetch_page(Criteria(markets=("in",)), offset=250)
    assert seen == [(250, 250)]
    assert page is not None and page.total == 812
    assert [listing.symbol for listing in page.listings] == ["TCS.NS"]
    tcs = page.listings[0]
    assert (tcs.name, tcs.exchange, tcs.currency) == ("Tata Consultancy Services Limited", "NSE", "INR")
    assert tcs.drawdown_pct == 26.1


async def test_a_failed_request_is_not_an_empty_page(monkeypatch):
    """'Yahoo did not answer' and 'nothing matched' must stay two different things."""
    import yfinance

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(yfinance, "screen", boom)
    assert await fetch_page(Criteria(markets=("us",))) is None

    monkeypatch.setattr(yfinance, "screen", lambda *args, **kwargs: {"total": 0, "quotes": []})
    empty = await fetch_page(Criteria(markets=("us",)))
    assert empty is not None and (empty.total, empty.listings) == (0, ())


def test_turnover_is_currency_free_and_reads_pence():
    """Shell: 3,722.5 pence × 10M shares a day against a £211B market cap."""
    shell = Listing.from_quote(
        _quote(symbol="SHEL.L", currency="GBp", regularMarketPrice=3722.5,
               marketCap=2.11e11, averageDailyVolume3Month=10_000_000)
    )
    assert shell is not None
    assert shell.daily_turnover == pytest.approx(37.225 * 1e7 / 2.11e11)
    no_volume = Listing.from_quote(_quote(averageDailyVolume3Month=None))
    assert no_volume is not None and no_volume.daily_turnover is None
    assert Listing.from_quote(_quote(fiftyTwoWeekHigh=None)).drawdown_pct is None


def test_listing_knows_when_it_trades_in_another_currency_than_it_reports():
    from data_sources.universe_source import Listing

    base = {"symbol": "X", "regularMarketPrice": 10.0, "marketCap": 1e9}
    home = Listing.from_quote({**base, "currency": "USD", "financialCurrency": "USD"})
    pence = Listing.from_quote({**base, "currency": "GBp", "financialCurrency": "GBP"})
    cedear = Listing.from_quote({**base, "currency": "ARS", "financialCurrency": "USD"})
    unknown = Listing.from_quote({**base, "currency": "USD"})
    assert home is not None and not home.cross_currency
    assert pence is not None and not pence.cross_currency  # pence are pounds
    assert cedear is not None and cedear.cross_currency
    assert unknown is not None and not unknown.cross_currency  # unknown is not "foreign"


def test_a_bound_becomes_a_condition_in_yahoos_units():
    from data_sources.universe_source import Bound, Measure

    conditions = _conditions(
        Criteria(
            markets=("us",),
            bounds=(
                Bound(Measure.RETURN_ON_ASSETS, at_least=8.0),
                Bound(Measure.DEBT_TO_EQUITY, at_most=0.5),  # 0.5× is 50 in Yahoo's percent
                Bound(Measure.QUICK_RATIO),  # neither bound: any listing Yahoo has a value for
            ),
        )
    )
    assert ("GTE", "returnonassets.lasttwelvemonths", 8.0) in conditions
    assert ("BTWN", "totaldebtequity.lasttwelvemonths", 0.0, 50.0) in conditions
    assert ("GTE", "quickratio.lasttwelvemonths", -1e12) in conditions


async def test_fetch_symbols_reads_every_page_and_reports_a_failure(monkeypatch):
    from data_sources.universe_source import fetch_symbols

    def paged(query, offset: int, size: int) -> dict[str, object] | None:
        quotes = [_quote(symbol=f"S{i}.NS") for i in range(offset, min(offset + size, 600))]
        return {"total": 600, "quotes": quotes}

    monkeypatch.setattr(universe_source, "_fetch_screen_sync", paged)
    symbols = await fetch_symbols(Criteria(markets=("in",)))
    assert symbols is not None and len(symbols) == 600 and "S599.NS" in symbols

    monkeypatch.setattr(universe_source, "_fetch_screen_sync", lambda *args: None)
    assert await fetch_symbols(Criteria(markets=("in",))) is None


def test_implied_return_on_equity_needs_one_currency_and_a_profit():
    listing = Listing.from_quote(_quote(epsTrailingTwelveMonths=142.6, bookValue=290.0, financialCurrency="INR"))
    assert listing is not None and listing.implied_roe_pct == 49.2
    loss = Listing.from_quote(_quote(epsTrailingTwelveMonths=-5.0, bookValue=290.0))
    assert loss is not None and loss.implied_roe_pct is None
    # Yahoo quotes an ADR's earnings and its book value in different currencies.
    adr = Listing.from_quote(_quote(currency="USD", financialCurrency="INR",
                                    epsTrailingTwelveMonths=1.7, bookValue=290.0))
    assert adr is not None and adr.implied_roe_pct is None
