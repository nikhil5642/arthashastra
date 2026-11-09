"""`screen` tests — a fake Yahoo screener per market, fake FX, real filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_sources import fx_source, universe_source
from fundamental_engine.cache import JsonStore
from fundamental_engine.commands import screen as screen_module
from fundamental_engine.commands.screen import ScreenRequest, ScreenSort, run_screen_command, screen
from fundamental_engine.engine import Engine, RefreshResult, RefreshStatus

RATES = {("USD", "INR"): 90.0, ("BRL", "INR"): 16.5}


def _quote(symbol: str, name: str, currency: str, price: float, market_cap: float,
           volume: float, *, high: float | None = None, pe: float | None = None) -> dict[str, object]:
    return {
        "symbol": symbol, "longName": name, "fullExchangeName": "X", "currency": currency,
        "regularMarketPrice": price, "marketCap": market_cap, "averageDailyVolume3Month": volume,
        "fiftyTwoWeekHigh": high if high is not None else price, "trailingPE": pe,
    }


MSFT = _quote("MSFT", "Microsoft Corporation", "USD", 530.0, 3.9e12, 23e6, high=559.0, pe=27.7)
SMALL = _quote("SMALL", "Small Co Inc", "USD", 10.0, 1e9, 1e6)
RELIANCE_NSE = _quote("RELIANCE.NS", "Reliance Industries Limited", "INR", 1315.0, 1.78e13, 1e7)
RELIANCE_BSE = _quote("RELIANCE.BO", "Reliance Industries Ltd", "INR", 1314.0, 1.78e13, 2e5)


class FakeYahoo:
    """Serves each market's quotes largest first, applying market-cap bounds like Yahoo."""

    def __init__(self, markets: dict[str, list[dict[str, object]]]) -> None:
        self.markets = markets
        self.requests: list[tuple[str, float, float | None, int]] = []

    def __call__(self, query, offset: int, size: int) -> dict[str, object]:
        conditions = {
            (c["operator"], c["operands"][0]): c["operands"][1]
            for c in query.to_dict()["operands"]
            if c["operator"] != "OR"
        }
        market = conditions[("EQ", "region")]
        low = conditions[("GTE", "intradaymarketcap")]
        high = conditions.get(("LTE", "intradaymarketcap"))
        self.requests.append((market, low, high, offset))
        quotes = sorted(
            (q for q in self.markets.get(market, [])
             if low <= q["marketCap"] and (high is None or q["marketCap"] <= high)),
            key=lambda q: -q["marketCap"],
        )
        return {"total": len(quotes), "quotes": quotes[offset: offset + size]}


@pytest.fixture
def yahoo(monkeypatch) -> FakeYahoo:
    fake = FakeYahoo(
        {"us": [MSFT, SMALL], "in": [RELIANCE_NSE, RELIANCE_BSE]}
    )
    monkeypatch.setattr(universe_source, "_fetch_screen_sync", fake)
    monkeypatch.setattr(fx_source, "_fetch_major_rate_sync", lambda f, t: RATES.get((f, t)))
    monkeypatch.setattr(screen_module, "REQUEST_SPACING_SECONDS", 0)
    monkeypatch.setattr(screen_module, "SCORE_SPACING_SECONDS", 0)
    return fake


async def test_market_caps_are_compared_in_the_base_currency(yahoo):
    result = await screen(
        ScreenRequest(markets=("us", "in"), min_market_cap=1e12), base_currency="INR"
    )
    # ₹1T is $11.1B on Nasdaq and ₹1T on the NSE: each market is bounded in its own currency.
    bounds = {market: low for market, low, _, _ in yahoo.requests if low}
    assert bounds == {"us": pytest.approx(1e12 / 90), "in": 1e12}
    assert [m.listing.symbol for m in result.matches] == ["MSFT", "RELIANCE.NS"]
    assert result.matches[0].market_cap_base == pytest.approx(3.9e12 * 90)
    assert result.listings_read == 3  # SMALL never left Yahoo; RELIANCE.BO was read, then merged


async def test_receipts_are_skipped_and_reading_continues_past_them(yahoo):
    receipts = [_quote(f"R{i}34.SA", f"US Company {i} DRN", "BRL", 50.0, 1e13 - i, 10.0)
                for i in range(250)]
    real = [_quote("PETR4.SA", "Petróleo Brasileiro S.A.", "BRL", 35.0, 4.5e11, 3e7),
            _quote("VALE3.SA", "Vale S.A.", "BRL", 60.0, 2.6e11, 2e7)]
    yahoo.markets["br"] = receipts + real
    result = await screen(ScreenRequest(markets=("br",)), base_currency="INR")
    assert [offset for market, _, _, offset in yahoo.requests] == [0, 250]
    assert [m.listing.symbol for m in result.matches] == ["PETR4.SA", "VALE3.SA"]
    assert (result.untradeable, result.listings_read) == (250, 252)


async def test_dip_and_pe_filters_and_drawdown_sort(yahoo):
    yahoo.markets["us"] = [
        _quote("DIP", "Dip Co", "USD", 70.0, 5e10, 1e7, high=100.0, pe=12.0),
        _quote("FLAT", "Flat Co", "USD", 90.0, 6e10, 1e7, high=100.0, pe=10.0),
        _quote("PRICEY", "Pricey Co", "USD", 50.0, 7e10, 1e7, high=100.0, pe=15.8),
        _quote("NOPE", "No PE Co", "USD", 60.0, 4e10, 1e7, high=100.0),
    ]
    result = await screen(
        ScreenRequest(markets=("us",), min_drawdown_pct=20, max_pe=15, sort=ScreenSort.DRAWDOWN),
        base_currency="INR",
    )
    # FLAT is only 10% down; PRICEY's quote shows a P/E the filter excluded.
    assert [(m.listing.symbol, m.listing.drawdown_pct) for m in result.matches] == [
        ("NOPE", 40.0), ("DIP", 30.0)
    ]
    by_yield = await screen(
        ScreenRequest(markets=("us",), sort=ScreenSort.EARNINGS_YIELD), base_currency="INR"
    )
    assert [m.listing.symbol for m in by_yield.matches] == ["FLAT", "DIP", "PRICEY", "NOPE"]


async def test_a_currency_without_any_fx_quote_is_dropped_and_counted(yahoo):
    yahoo.markets["us"] = [_quote("ODD", "Odd Co", "XYZ", 10.0, 1e10, 1e7)]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    assert (result.matches, result.without_fx) == ([], 1)


async def test_max_market_cap_skips_a_market_whose_listings_cant_be_converted(yahoo):
    yahoo.markets["us"] = [_quote("ODD", "Odd Co", "XYZ", 10.0, 1e10, 1e7)]
    result = await screen(ScreenRequest(markets=("us",), max_market_cap=1e12), base_currency="INR")
    assert result.matches == [] and result.listings_read == 0
    assert len(yahoo.requests) == 1  # the probe only


def _cache_score(cache: Path, ticker: str) -> None:
    JsonStore(cache / "scores.json").upsert(
        ticker,
        {
            "ticker": ticker, "leadership_score": 50.0, "fundamentals_score": 88.2,
            "valuation_score": 60.9, "prospects_score": 50.0, "news_score": 50.0,
            "composite": 65.0, "verdict": "HOLD", "confidence": 0.16,
            "data_completeness_pct": 40.0, "evidence": {}, "last_updated": "2026-11-09T10:00:00",
            "company_name": "Microsoft Corporation", "country": "United States",
        },
    )


def test_command_renders_scores_the_top_n_and_emits_json(yahoo, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cache"
    monkeypatch.setenv("DATA_CACHE_DIR", str(cache))
    monkeypatch.setenv("BASE_CURRENCY", "INR")
    monkeypatch.setenv("COLUMNS", "160")  # a terminal wide enough not to wrap names
    refreshed: list[str] = []

    async def fake_refresh(self: Engine, ticker: str) -> RefreshResult:
        refreshed.append(ticker)
        if ticker == "MSFT":
            _cache_score(cache, ticker)
            return RefreshResult(ticker=ticker, status=RefreshStatus.OK, composite=65.0, verdict="HOLD")
        return RefreshResult(ticker=ticker, status=RefreshStatus.NO_DATA)

    monkeypatch.setattr(Engine, "refresh_one", fake_refresh)
    assert run_screen_command(ScreenRequest(markets=("us",), score=2, max_pe=30)) == 0
    out = capsys.readouterr().out
    assert refreshed == ["MSFT", "SMALL"]
    assert "Microsoft Corporation" in out and "P/E 0–30" in out
    assert "88.2" in out and "no_data" in out
    assert "pick --amount <BUDGET> --tickers MSFT" in out

    assert run_screen_command(ScreenRequest(markets=("us",)), as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [m["ticker"] for m in payload["matches"]] == ["MSFT", "SMALL"]
    assert payload["matches"][0]["market_cap_base"] == round(3.9e12 * 90)
    assert payload["untradeable"] == 0 and payload["scored"] == []


def test_command_explains_an_empty_screen_and_rejects_unknown_values(yahoo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    assert run_screen_command(ScreenRequest(markets=("us",), min_drawdown_pct=99)) == 0
    assert "Nothing matched" in capsys.readouterr().out

    assert run_screen_command(ScreenRequest(markets=("atlantis",))) == 2
    assert "Unknown market(s): atlantis" in capsys.readouterr().out
    assert run_screen_command(ScreenRequest(sectors=("Crypto",))) == 2
    assert run_screen_command(ScreenRequest(markets=("us",), scan=0)) == 2
