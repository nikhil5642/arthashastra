"""SEC adapter tests — inline companyfacts payloads, no network."""

from __future__ import annotations

import math

from data_sources.fundamentals_source import sec_adapter
from data_sources.fundamentals_source.sec_adapter import build_annual_frames, covers


def _fact(start: str | None, end: str, val: float, filed: str, form: str = "10-K") -> dict:
    fact = {"end": end, "val": val, "filed": filed, "form": form}
    if start is not None:
        fact["start"] = start
    return fact


def _company(concepts: dict[str, list[dict]]) -> dict:
    return {"facts": {"us-gaap": {k: {"units": {"USD": v}} for k, v in concepts.items()}}}


def test_annual_frames_keep_first_filed_values_and_drop_quarters():
    facts = _company(
        {
            "NetIncomeLoss": [
                _fact("2024-07-01", "2025-06-30", 101.0, "2025-07-30"),
                _fact("2024-07-01", "2025-06-30", 99.0, "2026-07-29"),  # later restatement
                _fact("2025-04-01", "2025-06-30", 27.0, "2025-07-30"),  # quarter inside a 10-K
                _fact("2023-07-01", "2024-06-30", 88.0, "2024-07-30"),
            ],
            "Revenues": [
                _fact("2024-07-01", "2025-06-30", 280.0, "2025-07-30"),
                _fact("2023-07-01", "2024-06-30", 245.0, "2024-07-30"),
            ],
            "Assets": [_fact(None, "2025-06-30", 600.0, "2025-07-30")],
        }
    )
    frames = build_annual_frames(facts)
    assert frames is not None
    assert [c.date().isoformat() for c in frames.income.columns] == ["2025-06-30", "2024-06-30"]
    assert frames.income.loc["Net Income"].iloc[0] == 101.0  # first-filed, not restated
    assert frames.income.loc["Total Revenue"].iloc[1] == 245.0
    assert frames.balance.loc["Total Assets"].iloc[0] == 600.0
    assert math.isnan(frames.balance.loc["Total Assets"].iloc[1])


def test_eps_is_read_per_share_with_its_first_filing_date():
    facts = _company(
        {
            "NetIncomeLoss": [
                _fact("2012-09-30", "2013-09-28", 37_037.0, "2013-10-30"),
                _fact("2019-09-29", "2020-09-26", 57_411.0, "2020-10-30"),
            ],
        }
    )
    facts["facts"]["us-gaap"]["EarningsPerShareDiluted"] = {
        "units": {
            "USD/shares": [
                _fact("2012-09-30", "2013-09-28", 39.75, "2013-10-30"),
                _fact("2012-09-30", "2013-09-28", 5.68, "2015-10-28"),  # restated after the 2014 split
                _fact("2019-09-29", "2020-09-26", 3.28, "2020-10-30"),
            ]
        }
    }
    frames = build_annual_frames(facts)
    assert frames is not None
    assert list(frames.income.loc["Diluted EPS"]) == [3.28, 39.75]  # first-filed, as reported
    assert {end.isoformat(): filed.isoformat() for end, filed in frames.eps_filed.items()} == {
        "2013-09-28": "2013-10-30", "2020-09-26": "2020-10-30",
    }


def test_concepts_merge_by_priority_across_tag_changes():
    """Filers switched revenue tags in 2018; both eras must land in one row."""
    facts = _company(
        {
            "NetIncomeLoss": [
                _fact("2016-01-01", "2016-12-31", 10.0, "2017-02-01"),
                _fact("2019-01-01", "2019-12-31", 12.0, "2020-02-01"),
            ],
            "SalesRevenueNet": [_fact("2016-01-01", "2016-12-31", 100.0, "2017-02-01")],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                _fact("2019-01-01", "2019-12-31", 130.0, "2020-02-01")
            ],
        }
    )
    frames = build_annual_frames(facts)
    assert frames is not None
    assert list(frames.income.loc["Total Revenue"]) == [130.0, 100.0]


def test_derived_rows_match_yfinance_conventions():
    facts = _company(
        {
            "NetIncomeLoss": [_fact("2024-01-01", "2024-12-31", 50.0, "2025-02-01")],
            "OperatingIncomeLoss": [_fact("2024-01-01", "2024-12-31", 70.0, "2025-02-01")],
            "DepreciationDepletionAndAmortization": [
                _fact("2024-01-01", "2024-12-31", 10.0, "2025-02-01")
            ],
            "NetCashProvidedByUsedInOperatingActivities": [
                _fact("2024-01-01", "2024-12-31", 65.0, "2025-02-01")
            ],
            "PaymentsToAcquirePropertyPlantAndEquipment": [
                _fact("2024-01-01", "2024-12-31", 15.0, "2025-02-01")
            ],
            "LongTermDebtNoncurrent": [_fact(None, "2024-12-31", 40.0, "2025-02-01")],
            "LongTermDebtCurrent": [_fact(None, "2024-12-31", 5.0, "2025-02-01")],
        }
    )
    frames = build_annual_frames(facts)
    assert frames is not None
    assert frames.income.loc["EBITDA"].iloc[0] == 80.0
    assert frames.cashflow.loc["Capital Expenditure"].iloc[0] == -15.0  # yfinance sign
    assert frames.cashflow.loc["Free Cash Flow"].iloc[0] == 50.0
    assert frames.balance.loc["Total Debt"].iloc[0] == 45.0


def test_no_us_gaap_facts_means_no_frames():
    assert build_annual_frames({"facts": {"ifrs-full": {}}}) is None
    assert build_annual_frames({}) is None


def test_only_us_listings_are_covered():
    assert covers("MSFT")
    assert covers("BRK-B")
    assert not covers("005930.KS")
    assert not covers("RELIANCE.NS")


async def test_fetch_skips_foreign_listings_without_a_request(monkeypatch):
    async def boom(*_):
        raise AssertionError("no SEC request expected")

    monkeypatch.setattr(sec_adapter, "_get_json", boom)
    assert await sec_adapter.fetch_annual_frames("0700.HK", user_agent="test") is None


async def test_fetch_resolves_cik_then_builds_frames(monkeypatch):
    calls: list[str] = []

    async def fake_get_json(url: str, user_agent: str):
        calls.append(url)
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "RBRK", "cik_str": 1943896}}
        return _company(
            {"NetIncomeLoss": [_fact("2025-02-01", "2026-01-31", -350.0, "2026-03-20")]}
        )

    monkeypatch.setattr(sec_adapter, "_cik_by_ticker", {})
    monkeypatch.setattr(sec_adapter, "_get_json", fake_get_json)
    frames = await sec_adapter.fetch_annual_frames("rbrk", user_agent="test")
    assert frames is not None
    assert frames.income.loc["Net Income"].iloc[0] == -350.0
    assert calls[1].endswith("CIK0001943896.json")


def test_malformed_facts_are_ignored():
    facts = _company({
        "NetIncomeLoss": [
            {"end": "2025-12-31", "val": "lots", "filed": "2026-02-01", "form": "10-K",
             "start": "2025-01-01"},
            {"end": "not-a-date", "val": 1.0, "filed": "2026-02-01", "form": "10-K"},
            {"end": "2025-12-31", "val": True, "filed": "2026-02-01", "form": "10-K",
             "start": "2025-01-01"},
            {"end": "2025-12-31", "val": 5.0, "filed": "2026-02-01", "form": "8-K",
             "start": "2025-01-01"},
            "not even a dict",
        ],
        "Assets": [{"end": "2025-12-31", "val": 9.0, "filed": "2026-02-01", "form": "10-K",
                    "start": "2025-01-01"}],  # a balance with a start date is not a balance
    })
    assert build_annual_frames(facts) is None
    assert build_annual_frames({"facts": {"us-gaap": {"NetIncomeLoss": {"units": "USD"}}}}) is None


async def test_get_json_handles_errors(monkeypatch):
    import httpx

    real = httpx.AsyncClient

    def install(handler):
        class Client(real):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(sec_adapter.httpx, "AsyncClient", Client)

    install(lambda request: httpx.Response(200, json={"ok": True}))
    assert await sec_adapter._get_json("https://sec", "ua") == {"ok": True}
    install(lambda request: httpx.Response(403))
    assert await sec_adapter._get_json("https://sec", "ua") is None
    install(lambda request: httpx.Response(200, text="<html>not json</html>"))
    assert await sec_adapter._get_json("https://sec", "ua") is None

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    install(offline)
    assert await sec_adapter._get_json("https://sec", "ua") is None


async def test_unknown_ticker_has_no_cik(monkeypatch):
    async def mapping(url: str, user_agent: str):
        return {"0": {"ticker": "MSFT", "cik_str": 789019}, "1": "junk", "2": {"ticker": 5}}

    monkeypatch.setattr(sec_adapter, "_cik_by_ticker", {})
    monkeypatch.setattr(sec_adapter, "_get_json", mapping)
    assert await sec_adapter.fetch_annual_frames("NOTREAL", user_agent="ua") is None
