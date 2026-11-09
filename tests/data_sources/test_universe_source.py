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
            market="in", sectors=("Technology", "Healthcare"), max_pe=20, min_roe_pct=15,
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
    assert _conditions(Criteria(market="jp")) == {
        ("EQ", "region", "jp"),
        ("GTE", "intradaymarketcap", 0),
    }


def test_values_outside_yahoos_lists_are_rejected():
    with pytest.raises(ValueError):
        build_query(Criteria(market="atlantis"))
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
    page = await fetch_page(Criteria(market="in"), offset=250)
    assert seen == [(250, 250)]
    assert page.total == 812
    assert [listing.symbol for listing in page.listings] == ["TCS.NS"]
    tcs = page.listings[0]
    assert (tcs.name, tcs.exchange, tcs.currency) == ("Tata Consultancy Services Limited", "NSE", "INR")
    assert tcs.drawdown_pct == 26.1


async def test_failed_request_is_an_empty_page(monkeypatch):
    import yfinance

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(yfinance, "screen", boom)
    page = await fetch_page(Criteria(market="us"))
    assert (page.total, page.listings) == (0, ())


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
