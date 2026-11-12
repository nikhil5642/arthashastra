"""`screen` tests — a fake Yahoo screener, fake FX, real reading and filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_sources import fx_source, universe_source
from fundamental_engine import scorecard
from fundamental_engine.cache import JsonStore
from fundamental_engine.commands import screen as screen_module
from fundamental_engine.commands.screen import (
    Order,
    ScreenRequest,
    ScreenSort,
    SizeBand,
    resolve_names,
    run_screen_command,
    screen,
)
from fundamental_engine.engine import Engine, RefreshResult, RefreshStatus
from fundamental_engine.scorecard import RankBy

RATES = {("USD", "INR"): 90.0, ("BRL", "INR"): 16.5}

# One request the fake served: (markets asked, min market cap, max market cap, offset).
Request = tuple[tuple[str, ...], float, float | None, int]


def _quote(symbol: str, name: str, currency: str, price: float, market_cap: float,
           volume: float, *, high: float | None = None, pe: float | None = None,
           reports_in: str | None = None, exchange: str = "", sector: str = "",
           **ratios: float) -> dict[str, object]:
    """One screener row. `ratios` are what Yahoo filters on but never returns: roe=31.0, de=0.2, …"""
    return {
        "symbol": symbol, "longName": name, "fullExchangeName": "X", "exchange": exchange,
        "currency": currency, "regularMarketPrice": price, "marketCap": market_cap,
        "averageDailyVolume3Month": volume,
        "fiftyTwoWeekHigh": high if high is not None else price, "trailingPE": pe,
        "financialCurrency": reports_in or currency,
        "_sector": sector,
        "_ratios": {_RATIO_FIELDS[name]: value * (100 if name == "de" else 1)
                    for name, value in ratios.items()},
    }


# Short names for the tests → Yahoo's screener fields (D/E is stated in percent there).
_RATIO_FIELDS = {
    "roe": "returnonequity.lasttwelvemonths",
    "roce": "returnontotalcapital.lasttwelvemonths",
    "roa": "returnonassets.lasttwelvemonths",
    "de": "totaldebtequity.lasttwelvemonths",
    "current": "currentratio.lasttwelvemonths",
    "quick": "quickratio.lasttwelvemonths",
    "revenue_growth": "totalrevenues1yrgrowth.lasttwelvemonths",
    "profit_growth": "netincome1yrgrowth.lasttwelvemonths",
}
# A company at the top of every industrial curve, and one that clears nothing.
STELLAR = dict(roe=30.0, roce=28.0, roa=18.0, de=0.1, current=3.0, quick=2.5,
               revenue_growth=25.0, profit_growth=30.0)
WEAK = dict(roe=4.0, roce=5.0, roa=2.0, de=1.8, current=0.9, quick=0.5,
            revenue_growth=1.0, profit_growth=-10.0)


MSFT = _quote("MSFT", "Microsoft Corporation", "USD", 530.0, 3.9e12, 23e6, high=559.0, pe=27.7,
              exchange="NMS")
SMALL = _quote("SMALL", "Small Co Inc", "USD", 10.0, 1e9, 1e6, exchange="NMS")
RELIANCE_NSE = _quote("RELIANCE.NS", "Reliance Industries Limited", "INR", 1315.0, 1.78e13, 1e7,
                      exchange="NSI")
RELIANCE_BSE = _quote("RELIANCE.BO", "Reliance Industries Ltd", "INR", 1314.0, 1.78e13, 2e5,
                      exchange="BSE")


class FakeYahoo:
    """Serves the asked markets' quotes largest first, applying its filters like Yahoo.

    `requests` are the reads of a screen; `questions` are the preview's
    follow-ups, each recorded as (ratio field, floor, ceiling).
    """

    def __init__(self, markets: dict[str, list[dict[str, object]]]) -> None:
        self.markets = markets
        self.requests: list[Request] = []
        self.questions: list[tuple[str, float, float | None]] = []
        self.offline = False  # no request is answered
        self.unanswered: set[str] = set()  # markets a single-market request gets no answer for
        self.questions_unanswered = False  # reads work, the preview's questions don't

    def __call__(self, query, offset: int, size: int) -> dict[str, object] | None:
        asked: tuple[str, ...] = ()
        low, high = 0.0, None
        sectors: set[str] = set()
        ratio: tuple[str, float, float | None] | None = None
        for clause in query.to_dict()["operands"]:
            operator, operands = clause["operator"], clause["operands"]
            field = operands[0]["operands"][0] if operator == "OR" else operands[0]
            if field == "region":
                asked = tuple(o["operands"][1] for o in operands) if operator == "OR" else (operands[1],)
            elif field == "sector":
                sectors = {o["operands"][1] for o in operands} if operator == "OR" else {operands[1]}
            elif field == "intradaymarketcap":
                low, high = (operands[1], high) if operator == "GTE" else (low, operands[1])
            elif field in _RATIO_FIELDS.values():
                ratio = (field, operands[1], operands[2] if operator == "BTWN" else None)

        if ratio is not None or sectors:
            self.questions.append(ratio or ("sector", 0.0, None))
            if self.questions_unanswered:
                return None
        else:
            self.requests.append((asked, low, high, offset))
        if self.offline or (len(asked) == 1 and asked[0] in self.unanswered):
            return None
        quotes = sorted(
            (q for market in asked for q in self.markets.get(market, [])
             if low <= q["marketCap"] and (high is None or q["marketCap"] <= high)
             and (not sectors or q["_sector"] in sectors) and _passes(q, ratio)),
            key=lambda q: -q["marketCap"],
        )
        return {"total": len(quotes), "quotes": quotes[offset: offset + size]}


def _passes(quote: dict[str, object], ratio: tuple[str, float, float | None] | None) -> bool:
    """Yahoo's filter on one ratio: a listing it has no value for never passes."""
    if ratio is None:
        return True
    field, floor, ceiling = ratio
    value = quote["_ratios"].get(field)
    return value is not None and value >= floor and (ceiling is None or value <= ceiling)


@pytest.fixture
def yahoo(monkeypatch) -> FakeYahoo:
    fake = FakeYahoo({"us": [MSFT, SMALL], "in": [RELIANCE_NSE, RELIANCE_BSE]})
    monkeypatch.setattr(universe_source, "_fetch_screen_sync", fake)
    monkeypatch.setattr(fx_source, "_fetch_major_rate_sync", lambda f, t: RATES.get((f, t)))
    monkeypatch.setattr(screen_module, "REQUEST_SPACING_SECONDS", 0)
    monkeypatch.setattr(screen_module, "RETRY_PAUSE_SECONDS", 0)
    monkeypatch.setattr(scorecard, "FETCH_START_SPACING_SECONDS", 0)
    return fake


@pytest.fixture
def market_by_market(monkeypatch) -> None:
    """Make every screen 'too broad to read whole', so it is read one market at a time."""
    monkeypatch.setattr(screen_module, "WHOLE_READ_LIMIT", 0)


# ── Reading: whole, or market by market ──────────────────────────────────────


async def test_a_narrow_screen_is_read_whole_in_one_request(yahoo):
    result = await screen(
        ScreenRequest(markets=("us", "in"), min_market_cap=1e12), base_currency="INR"
    )
    # One query for both markets, with no market-cap bound: it can't be stated in two currencies.
    assert yahoo.requests == [(("us", "in"), 0, None, 0)]
    # The ₹1T bound is applied per listing, in rupees: SMALL ($1B = ₹90B) falls out here.
    assert [(m.listing.symbol, m.market) for m in result.matches] == [
        ("MSFT", "us"), ("RELIANCE.NS", "in"),
    ]
    assert result.matches[0].market_cap_base == pytest.approx(3.9e12 * 90)
    assert (result.listings_read, result.markets_read, result.unread_markets) == (4, 2, [])


async def test_a_broad_screen_is_read_market_by_market_with_local_bounds(yahoo, market_by_market):
    result = await screen(
        ScreenRequest(markets=("us", "in"), min_market_cap=1e12), base_currency="INR"
    )
    # ₹1T is $11.1B on Nasdaq and ₹1T on the NSE: each market is bounded in its own currency.
    bounds = {asked[0]: low for asked, low, _, _ in yahoo.requests if low}
    assert bounds == {"us": pytest.approx(1e12 / 90), "in": 1e12}
    assert [m.listing.symbol for m in result.matches] == ["MSFT", "RELIANCE.NS"]
    assert result.listings_read == 3  # SMALL never left Yahoo; RELIANCE.BO was read, then merged


@pytest.mark.parametrize("whole", [True, False])
async def test_receipts_are_skipped_and_reading_continues_past_them(yahoo, monkeypatch, whole):
    if not whole:
        monkeypatch.setattr(screen_module, "WHOLE_READ_LIMIT", 0)
    receipts = [_quote(f"R{i}34.SA", f"US Company {i} DRN", "BRL", 50.0, 1e13 - i, 10.0)
                for i in range(250)]
    real = [_quote("PETR4.SA", "Petróleo Brasileiro S.A.", "BRL", 35.0, 4.5e11, 3e7),
            _quote("VALE3.SA", "Vale S.A.", "BRL", 60.0, 2.6e11, 2e7)]
    yahoo.markets["br"] = receipts + real
    result = await screen(ScreenRequest(markets=("br",)), base_currency="INR")
    # Read whole: two pages. Market by market: the same two, after the request that sized the screen.
    assert [offset for _, _, _, offset in yahoo.requests] == ([0, 250] if whole else [0, 0, 250])
    assert [(m.listing.symbol, m.market) for m in result.matches] == [
        ("PETR4.SA", "br"), ("VALE3.SA", "br"),
    ]
    assert (result.untradeable, result.listings_read) == (250, 252)


async def test_receipts_with_a_programme_size_market_cap_are_skipped(yahoo, monkeypatch):
    """CEDEARs trade actively, so turnover can't catch them — their impossible P/E does."""
    monkeypatch.setitem(RATES, ("ARS", "INR"), 0.065)
    yahoo.markets["ar"] = [
        _quote("GOOGL.BA", "Alphabet Inc.", "ARS", 8190.0, 3.47e12, 1.1e6, pe=0.01, reports_in="USD"),
        _quote("XOM.BA", "Exxon Mobil Corporation", "ARS", 27000.0, 2.2e13, 3e5, pe=0.39, reports_in="USD"),
        _quote("YPFD.BA", "YPF Sociedad Anónima", "ARS", 6450.0, 2.5e13, 2.1e6, pe=8.2),
        # A real company on a one-off gain: tiny P/E, but in its own reporting currency.
        _quote("GAIN.BA", "Ganancia S.A.", "ARS", 100.0, 1e12, 1e7, pe=1.1),
    ]
    result = await screen(ScreenRequest(markets=("ar",)), base_currency="INR")
    assert [m.listing.symbol for m in result.matches] == ["YPFD.BA", "GAIN.BA"]
    assert result.untradeable == 2


async def test_a_home_currency_listing_beats_a_more_traded_receipt(yahoo, monkeypatch):
    monkeypatch.setitem(RATES, ("TWD", "INR"), 2.9)
    name = "Taiwan Semiconductor Manufacturing Company Limited"
    yahoo.markets["us"] = [_quote("TSM", name, "USD", 388.0, 2.0e12, 1.1e7, pe=25.2, reports_in="TWD")]
    yahoo.markets["tw"] = [_quote("2330.TW", name, "TWD", 2235.0, 5.8e13, 3e7, pe=22.9)]
    result = await screen(ScreenRequest(markets=("us", "tw")), base_currency="INR")
    # TSM trades ₹384T a day against 2330.TW's ₹194T, but only 2330.TW is priced in the
    # currency the company reports in — the listing whose ratios can all be computed.
    assert [m.listing.symbol for m in result.matches] == ["2330.TW"]


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


async def test_max_market_cap_skips_a_market_whose_listings_cant_be_converted(
    yahoo, market_by_market
):
    yahoo.markets["us"] = [_quote("ODD", "Odd Co", "XYZ", 10.0, 1e10, 1e7)]
    result = await screen(ScreenRequest(markets=("us",), max_market_cap=1e12), base_currency="INR")
    assert result.matches == [] and result.listings_read == 0
    assert len(yahoo.requests) == 2  # the request that sized the screen, then the market's probe


# ── When Yahoo does not answer ───────────────────────────────────────────────


async def test_a_market_yahoo_does_not_answer_for_is_named_not_dropped(yahoo, market_by_market):
    yahoo.unanswered = {"in"}
    result = await screen(ScreenRequest(markets=("us", "in")), base_currency="INR")
    assert [m.listing.symbol for m in result.matches] == ["MSFT", "SMALL"]
    assert (result.unread_markets, result.markets_read) == (["in"], 1)
    assert [asked for asked, _, _, _ in yahoo.requests].count(("in",)) == 2  # asked, then once more


async def test_when_yahoo_is_down_the_screen_says_so_after_two_requests(yahoo):
    yahoo.offline = True
    result = await screen(ScreenRequest(markets=("us", "in")), base_currency="INR")
    assert result.matches == [] and result.unread_markets == ["us", "in"]
    assert len(yahoo.requests) == 2  # one try, one retry — not one per market


async def test_reading_stops_after_three_markets_in_a_row_go_unanswered(yahoo, market_by_market):
    markets = ("us", "in", "jp", "tw", "kr", "de")
    yahoo.unanswered = {"in", "jp", "tw", "kr", "de"}
    result = await screen(ScreenRequest(markets=markets), base_currency="INR")
    assert result.unread_markets == ["in", "jp", "tw", "kr", "de"]
    asked_alone = {asked[0] for asked, _, _, _ in yahoo.requests if len(asked) == 1}
    assert asked_alone == {"us", "in", "jp", "tw"}  # kr and de were not even tried


def test_an_incomplete_screen_is_announced(yahoo, market_by_market, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("COLUMNS", "160")
    yahoo.unanswered = {"in"}
    assert run_screen_command(ScreenRequest(markets=("us", "in"))) == 0
    assert "did not answer for: in — this list is incomplete" in capsys.readouterr().out
    assert run_screen_command(ScreenRequest(markets=("us", "in")), as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["unread_markets"] == ["in"]


# ── Order: the rubric preview, blind to company size ─────────────────────────


def _score(match) -> float | None:
    return match.preview.score if match.preview else None


def _chip(symbol: str, market_cap: float, **ratios: float) -> dict[str, object]:
    return _quote(symbol, f"{symbol} Semiconductor", "USD", 50.0, market_cap, 1e7,
                  exchange="NMS", sector="Technology", **ratios)


async def test_a_small_company_that_clears_the_marks_comes_before_a_giant_that_does_not(yahoo):
    yahoo.markets["us"] = [
        _chip("GIANT", 4e12, **WEAK),
        _chip("MIDCO", 6e9, **(STELLAR | {"roa": 9.0, "current": 1.6})),  # good, not excellent, on two
        _chip("TINY", 9e7, **STELLAR),
    ]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    assert result.order is Order.PREVIEW and result.read_whole
    assert [(m.listing.symbol, m.size_band, _score(m)) for m in result.matches] == [
        ("TINY", SizeBand.SMALL, 100.0),
        ("MIDCO", SizeBand.MID, 89.4),  # half marks on two inputs: 6 of the 56 screenable points short
        ("GIANT", SizeBand.LARGE, 0.0),
    ]
    assert result.size_mix() == {SizeBand.LARGE: 1, SizeBand.MID: 1, SizeBand.SMALL: 1}
    # Three questions for each of the eight marks, and one to find the lenders.
    assert len(yahoo.questions) == 3 * 8 + 1


async def test_companies_level_on_the_preview_take_turns_across_the_size_bands(yahoo):
    caps = [9e11, 8e11, 7e11, 6e9, 5e9, 4e9, 3e7, 2e7, 1e7]
    yahoo.markets["us"] = [_chip(f"C{i}", cap, **STELLAR) for i, cap in enumerate(caps)]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    # All nine clear every mark. Largest-first would list C0 C1 C2 and never reach a small one.
    assert [m.listing.symbol for m in result.matches] == [
        "C0", "C3", "C6", "C1", "C4", "C7", "C2", "C5", "C8",
    ]
    assert [m.size_band for m in result.matches[:3]] == [SizeBand.LARGE, SizeBand.MID, SizeBand.SMALL]


async def test_a_ratio_yahoo_has_no_value_for_is_left_out_not_counted_against(yahoo):
    gaps = {k: v for k, v in STELLAR.items() if k not in ("quick", "revenue_growth")}
    yahoo.markets["us"] = [
        _chip("FULL", 5e11, **(STELLAR | {"roa": 9.0})),  # everything known, one mark only 'good'
        _chip("GAPS", 4e7, **gaps),  # a small company: two ratios missing, the rest excellent
        _chip("BLANK", 3e7),  # nothing known: no preview at all, and last
    ]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    previews = {m.listing.symbol: m.preview for m in result.matches}
    # Judged on the six ratios Yahoo knows, GAPS is perfect — and it is not marked down for the other two.
    assert (previews["GAPS"].score, previews["GAPS"].coverage, previews["GAPS"].thin) == (100.0, 0.77, False)
    assert previews["FULL"].score == 94.0 and previews["BLANK"] is None
    assert [m.listing.symbol for m in result.matches] == ["GAPS", "FULL", "BLANK"]


async def test_a_preview_resting_on_a_few_known_ratios_does_not_outrank_a_well_covered_one(yahoo):
    """A month-old listing with IPO cash and nothing else on file is not the best company in the screen."""
    yahoo.markets["us"] = [
        _chip("PROVEN", 5e10, **(STELLAR | {"roa": 9.0})),  # 94 on all eight marks
        _chip("NEWLY_LISTED", 6e10, current=9.0, quick=8.5),  # 100 — on two liquidity ratios alone
    ]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    by_symbol = {m.listing.symbol: m.preview for m in result.matches}
    assert (by_symbol["NEWLY_LISTED"].score, by_symbol["NEWLY_LISTED"].thin) == (100.0, True)
    assert [m.listing.symbol for m in result.matches] == ["PROVEN", "NEWLY_LISTED"]


async def test_lenders_are_previewed_on_the_bank_rubrics_marks(yahoo):
    bank = dict(roe=17.0, roa=1.6, profit_growth=9.0)  # a good bank; hopeless on industrial marks
    yahoo.markets["us"] = [
        _chip("CHIPCO", 5e10, **STELLAR),
        _quote("GOODBANK", "Good Bank Corp", "USD", 40.0, 4e10, 1e7, exchange="NYQ",
               sector="Financial Services", **bank),
    ]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    previews = {m.listing.symbol: _score(m) for m in result.matches}
    # ROE 17 and ROA 1.6 are 'good' (half marks), profit growth 9 is 'good': 50 of 100.
    assert previews == {"CHIPCO": 100.0, "GOODBANK": 50.0}

    # Asked for a bank industry outright, nobody needs to be told who the lenders are.
    yahoo.questions.clear()
    banks = await screen(ScreenRequest(markets=("us",), industries=("Banks—Regional",)), base_currency="INR")
    assert ("sector", 0.0, None) not in yahoo.questions and len(yahoo.questions) == 3 * 3
    assert {m.listing.symbol: _score(m) for m in banks.matches}["GOODBANK"] == 50.0


async def test_asking_for_the_largest_is_the_only_way_to_get_them_first(yahoo):
    yahoo.markets["us"] = [_chip("GIANT", 4e12, **WEAK), _chip("TINY", 9e7, **STELLAR)]
    result = await screen(ScreenRequest(markets=("us",), sort=ScreenSort.MARKET_CAP), base_currency="INR")
    assert [m.listing.symbol for m in result.matches] == ["GIANT", "TINY"]
    assert result.order is Order.AS_SORTED and yahoo.questions == []  # no preview was needed


async def test_too_many_listings_to_preview_are_spread_evenly_across_sizes(yahoo, monkeypatch):
    monkeypatch.setattr(screen_module, "PREVIEW_LIMIT", 5)
    caps = [9e11, 8e11, 7e9, 6e9, 5e7, 4e7]
    yahoo.markets["us"] = [_chip(f"C{i}", cap, **STELLAR) for i, cap in enumerate(caps)]
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    assert result.order is Order.SIZE_SPREAD and yahoo.questions == []
    assert [m.listing.symbol for m in result.matches] == ["C0", "C2", "C4", "C1", "C3", "C5"]
    assert all(m.preview is None for m in result.matches)


async def test_a_preview_yahoo_does_not_answer_falls_back_to_an_even_spread(yahoo):
    yahoo.markets["us"] = [_chip("GIANT", 4e12, **WEAK), _chip("TINY", 9e7, **STELLAR)]
    yahoo.questions_unanswered = True
    result = await screen(ScreenRequest(markets=("us",)), base_currency="INR")
    assert result.order is Order.SIZE_SPREAD
    assert [m.listing.symbol for m in result.matches] == ["GIANT", "TINY"]  # one from each band, no claim of quality


def test_a_screen_too_broad_to_read_whole_says_small_companies_are_missing(
    yahoo, market_by_market, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("COLUMNS", "200")
    assert run_screen_command(ScreenRequest(markets=("us", "in"))) == 0
    out = capsys.readouterr().out
    assert "Too broad to read whole" in out and "smaller companies are missing" in out

    assert run_screen_command(ScreenRequest(markets=("us", "in")), as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload["read_whole"], payload["ordered_by"]) == (False, "size_spread")


def test_the_screen_says_how_the_sizes_were_covered(yahoo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BASE_CURRENCY", "USD")
    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.setitem(RATES, ("USD", "USD"), 1.0)
    yahoo.markets["us"] = [
        _chip("GIANT", 4e12, **WEAK), _chip("MIDCO", 6e9, **STELLAR), _chip("TINY", 9e7, **STELLAR),
    ]
    assert run_screen_command(ScreenRequest(markets=("us",))) == 0
    out = capsys.readouterr().out
    assert "By size: 1 large · 1 mid (up to $6.00B) · 1 small (up to $90.00M)" in out
    assert "Ordered by rubric preview" in out and "Size plays no part" in out
    assert "3 of 3 companies — best rubric preview first" in out
    assert out.index("MIDCO") < out.index("TINY Semi") < out.index("GIANT Semi")


# ── Names: what was typed → Yahoo's own spelling ─────────────────────────────


def test_names_are_matched_whatever_their_case_or_punctuation():
    request = resolve_names(
        ScreenRequest(
            markets=("US", "in"),
            sectors=("tech",),
            industries=("Semiconductors", "software infrastructure", "BANK"),
        )
    )
    assert request.markets == ("us", "in")
    assert request.sectors == ("Technology",)
    # An exact name is only itself; anything else is every name containing the words.
    assert request.industries == (
        "Semiconductors", "Software—Infrastructure", "Banks—Diversified", "Banks—Regional",
    )
    both = resolve_names(ScreenRequest(industries=("semiconductor", "Semiconductors")))
    assert both.industries == ("Semiconductor Equipment & Materials", "Semiconductors")


def test_names_that_match_nothing_are_named_with_the_valid_ones():
    with pytest.raises(ValueError, match=r"Unknown industry\(s\): chips, crypto\. Valid: .*Semiconductors"):
        resolve_names(ScreenRequest(industries=("chips", "bank", "crypto")))
    # A market code is matched whole: "i" is not India, Indonesia and Italy.
    with pytest.raises(ValueError, match=r"Unknown market\(s\): i\."):
        resolve_names(ScreenRequest(markets=("i",)))
    with pytest.raises(ValueError, match="at least 1"):
        resolve_names(ScreenRequest(scan=0))


# ── The command ──────────────────────────────────────────────────────────────


def _cache_score(cache: Path, ticker: str, *, fundamentals: float, valuation: float) -> None:
    JsonStore(cache / "scores.json").upsert(
        ticker,
        {
            "ticker": ticker, "leadership_score": 50.0, "fundamentals_score": fundamentals,
            "valuation_score": valuation, "prospects_score": 50.0, "news_score": 50.0,
            "composite": 65.0, "verdict": "HOLD", "confidence": 0.16,
            "data_completeness_pct": 40.0, "last_updated": "2026-11-09T10:00:00",
            "evidence": {"fundamentals": ["ROE = 31.0% → 100/100"]},
            "unscored": ["leadership", "prospects", "news"],
            "company_name": f"{ticker} Corporation", "country": "United States",
        },
    )


def test_command_scores_the_top_n_ranks_them_and_emits_json(yahoo, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cache"
    monkeypatch.setenv("DATA_CACHE_DIR", str(cache))
    monkeypatch.setenv("BASE_CURRENCY", "INR")
    monkeypatch.setenv("COLUMNS", "200")  # a terminal wide enough not to wrap names
    yahoo.markets["us"] = [
        MSFT, _quote("ORCL", "Oracle Corporation", "USD", 200.0, 5e11, 1e7, exchange="NYQ"), SMALL,
    ]
    refreshed: list[str] = []

    async def fake_refresh(self: Engine, ticker: str, *, follow: bool = True) -> RefreshResult:
        refreshed.append(ticker)
        if ticker == "SMALL":
            return RefreshResult(ticker=ticker, status=RefreshStatus.NO_DATA)
        scores = {"MSFT": (88.2, 40.0), "ORCL": (70.0, 90.0)}[ticker]
        _cache_score(cache, ticker, fundamentals=scores[0], valuation=scores[1])
        return RefreshResult(ticker=ticker, status=RefreshStatus.OK, composite=65.0, verdict="HOLD")

    monkeypatch.setattr(Engine, "refresh_one", fake_refresh)
    request = ScreenRequest(markets=("us",), score=3, max_pe=30, rank_by=RankBy.FUNDAMENTALS)
    assert run_screen_command(request) == 0
    out = capsys.readouterr().out
    assert refreshed == ["MSFT", "ORCL", "SMALL"]
    assert "Microsoft Corporation" in out and "P/E 0–30" in out
    assert "ranked by fundamentals" in out and out.index("88.2") < out.index("70.0")
    assert "not judged" in out and "Not scored: SMALL (no_data)" in out
    assert "pick --amount <BUDGET> --tickers MSFT ORCL" in out

    by_price = ScreenRequest(markets=("us",), score=2, rank_by=RankBy.VALUATION)
    assert run_screen_command(by_price, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [m["ticker"] for m in payload["matches"]] == ["MSFT", "ORCL", "SMALL"]
    assert payload["matches"][0]["market_cap_base"] == round(3.9e12 * 90)
    assert payload["ranked_by"] == "valuation"
    assert [(c["ticker"], c["valuation"]) for c in payload["scored"]] == [("ORCL", 90.0), ("MSFT", 40.0)]
    card = payload["scored"][1]
    assert card["why"]["fundamentals"] == ["ROE = 31.0% → 100/100"]
    assert card["awaiting_judgment"] == ["leadership", "prospects", "news"]
    assert payload["not_scored"] == [] and payload["untradeable"] == 0
    # How the sizes were covered is part of the answer.
    assert payload["size_mix"] == {"large": 1, "mid": 1, "small": 1}
    assert payload["scored_size_mix"] == {"large": 1, "mid": 1, "small": 0}
    assert {c["ticker"]: c["size_band"] for c in payload["scored"]} == {"MSFT": "large", "ORCL": "mid"}
    assert [m["ticker"] for m in payload["next_in_line"]] == ["SMALL"]
    assert (payload["ordered_by"], payload["read_whole"]) == ("preview", True)

    assert run_screen_command(ScreenRequest(markets=("us",)), as_json=True) == 0
    bare = json.loads(capsys.readouterr().out)
    assert bare["scored"] == [] and bare["filters"] == [] and bare["unread_markets"] == []
    assert bare["next_in_line"] == []  # nothing was scored, so nothing is "next"


def test_command_explains_an_empty_screen_and_rejects_unknown_values(yahoo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    assert run_screen_command(ScreenRequest(markets=("us",), min_drawdown_pct=99)) == 0
    assert "Nothing matched" in capsys.readouterr().out

    assert run_screen_command(ScreenRequest(markets=("atlantis",))) == 2
    assert "Unknown market(s): atlantis" in capsys.readouterr().out
    assert run_screen_command(ScreenRequest(sectors=("Crypto",))) == 2
    assert run_screen_command(ScreenRequest(markets=("us",), scan=0)) == 2
