"""Resolver tests — mock Yahoo search to stay hermetic."""

from __future__ import annotations

from types import SimpleNamespace

from fundamental_engine import resolver
from fundamental_engine.resolver import TickerMatch, pick_best, resolve_name


def _fake_search(quotes: list[dict]):
    return lambda query, max_results: SimpleNamespace(quotes=quotes)


async def test_resolve_finds_listings_on_any_exchange(monkeypatch):
    import yfinance

    monkeypatch.setattr(
        yfinance,
        "Search",
        _fake_search(
            [
                {"symbol": "MSFT", "quoteType": "EQUITY", "exchDisp": "NASDAQ",
                 "shortname": "Microsoft Corporation", "longname": "Microsoft Corporation"},
                {"symbol": "MSFT.MX", "quoteType": "EQUITY", "exchDisp": "Mexico",
                 "shortname": "MICROSOFT CORP", "longname": "Microsoft Corporation"},
                {"symbol": "MSFU", "quoteType": "ETF", "exchDisp": "NASDAQ",
                 "shortname": "Direxion MSFT Bull 2X", "longname": "Direxion MSFT Bull 2X"},
            ]
        ),
    )
    matches = await resolve_name("Microsoft Corp")
    assert [m.ticker for m in matches] == ["MSFT", "MSFT.MX"]  # ETF dropped, Yahoo order kept
    assert matches[0].exchange == "NASDAQ"


async def test_resolve_keeps_exchange_suffixes(monkeypatch):
    import yfinance

    monkeypatch.setattr(
        yfinance,
        "Search",
        _fake_search(
            [{"symbol": "0700.HK", "quoteType": "EQUITY", "exchDisp": "HKSE",
              "shortname": "TENCENT", "longname": "Tencent Holdings Limited"}]
        ),
    )
    [match] = await resolve_name("Tencent Holdings")
    assert match.ticker == "0700.HK"


async def test_resolve_name_empty_list(monkeypatch):
    monkeypatch.setattr(resolver, "_resolve_sync", lambda q, l: [])
    assert await resolve_name("nosuchcompany") == []


def test_query_variants_strip_legal_suffixes():
    assert resolver._query_variants("Reliance Industries Ltd") == [
        "Reliance Industries Ltd",
        "Reliance Industries",
        "Reliance",
    ]


def test_pick_best_and_to_dict():
    m1 = TickerMatch("GOOGL", "NASDAQ", "Alphabet Inc.", "Alphabet Inc.", 160.0)
    m2 = TickerMatch("ABEA.DE", "XETRA", "ALPHABET INC.CL.A", "Alphabet Inc.", 60.0)
    assert pick_best([m1, m2]) is m1
    assert pick_best([]) is None
    assert m1.to_dict() == {
        "ticker": "GOOGL",
        "exchange": "NASDAQ",
        "short_name": "Alphabet Inc.",
        "long_name": "Alphabet Inc.",
        "score": 160.0,
    }


async def test_search_failures_and_symbol_less_quotes_return_nothing(monkeypatch):
    import yfinance

    def boom(query, max_results):
        raise ConnectionError("Yahoo search down")

    monkeypatch.setattr(yfinance, "Search", boom)
    assert await resolve_name("Samsung Electronics") == []

    monkeypatch.setattr(
        yfinance, "Search",
        _fake_search([{"quoteType": "EQUITY", "shortname": "No symbol"}]),
    )
    assert await resolve_name("No symbol Inc") == []
