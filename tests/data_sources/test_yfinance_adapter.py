"""yfinance adapter tests — a fake Samsung (KRW) ticker built from DataFrames.

`yfinance.Ticker` is monkeypatched; everything else is the real adapter.
"""

from __future__ import annotations

import pandas as pd
import pytest

from data_sources.fundamentals_source import yfinance_adapter

YEARS = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
QUARTERS = pd.to_datetime(["2026-09-30", "2026-06-30", "2025-09-30"])


class FakeSamsung:
    """Yahoo's shapes for 005930.KS, scaled down (KRW)."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, auto_adjust: bool) -> pd.DataFrame:
        # Split-adjusted closes only: dividends would shrink every past P/E.
        assert auto_adjust is False
        days = pd.to_datetime(["2018-05-03", "2018-05-04", "2026-11-04", "2026-11-05"])
        return pd.DataFrame(
            {"Close": [1060.0, 1020.0, 57500.0, 57900.0], "Adj Close": [980.0, 945.0, 57500.0, 57900.0],
             "High": [1080.0, 1050.0, 58200.0, 58100.0], "Low": [1000.0, 1010.0, 56500.0, 57400.0],
             "Stock Splits": [0.0, 50.0, 0.0, 0.0]},
            index=days,
        )

    income_stmt = pd.DataFrame(
        {
            YEARS[0]: [300.0, 120.0, 40.0, 45.0, 0.8, 34.0, 50.0, 10.0, 70.0, 16.0, 5.0],
            YEARS[1]: [258.0, 105.0, 30.0, 40.0, 0.7, 25.0, 40.0, 9.0, 60.0, 14.0, 3.7],
            YEARS[2]: [260.0, 100.0, 25.0, 38.0, 0.6, 20.0, 33.0, 8.0, 55.0, 13.0, float("nan")],
            YEARS[3]: [200.0, 80.0, 30.0, 35.0, 0.5, 28.0, 45.0, 9.0, 62.0, 11.0, 4.1],
        },
        index=["Total Revenue", "Gross Profit", "Operating Income", "Selling General And Administration",
               "Interest Expense", "Net Income", "Pretax Income", "Tax Provision", "EBITDA",
               "Cost Of Revenue", "Diluted EPS"],
    )
    balance_sheet = pd.DataFrame(
        {YEARS[0]: [500.0, 400.0, 20.0, 200.0, 80.0, 50.0, 60.0, 30.0, 25.0, 10.0, 380.0]},
        index=["Total Assets", "Stockholders Equity", "Total Debt", "Current Assets",
               "Current Liabilities", "Inventory", "Cash And Cash Equivalents",
               "Accounts Receivable", "Accounts Payable", "Goodwill", "Tangible Book Value"],
    )
    cashflow = pd.DataFrame(
        {YEARS[0]: [45.0, -20.0]}, index=["Operating Cash Flow", "Capital Expenditure"]
    )
    quarterly_income_stmt = pd.DataFrame(
        {QUARTERS[0]: [86.0, 12.0], QUARTERS[1]: [74.0, 9.0], QUARTERS[2]: [79.0, 10.0]},
        index=["Total Revenue", "Net Income"],
    )
    info = {
        "longName": "Samsung Electronics Co., Ltd.", "sector": "Technology",
        "industry": "Consumer Electronics", "country": "South Korea",
        "fullExchangeName": "KSE", "currency": "KRW", "financialCurrency": "KRW",
        "marketCap": 3.4e14, "heldPercentInsiders": 0.2, "heldPercentInstitutions": 0.35,
        "trailingPE": 12.5, "trailingAnnualDividendYield": 0.0185, "bookValue": 57000.0,
        "fullTimeEmployees": 125000, "longBusinessSummary": "Makes chips and phones.",
        "targetMeanPrice": 75000.0, "recommendationMean": 1.6, "numberOfAnalystOpinions": 40,
    }


@pytest.fixture
def samsung(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", FakeSamsung)


def test_snapshot_fields_for_a_korean_listing(samsung):
    out, prices = yfinance_adapter._fetch_yfinance_sync("005930.KS")

    assert prices[-1] == ("2026-11-05", 57900.0)
    assert prices[0] == ("2018-05-03", 1060.0)  # split-adjusted, not dividend-adjusted
    assert (out["current_price"], out["high_52w"], out["low_52w"]) == (57900.0, 58200.0, 1000.0)
    assert out["latest_annual_period"] == "2025-12-31"

    assert out["debt_to_equity"] == 0.05
    assert out["current_ratio"] == 2.5
    assert out["quick_ratio"] == round((200 - 50) / 80, 3)
    assert out["interest_coverage"] == 50.0  # Operating Income 40 / interest 0.8
    assert out["net_debt_to_ebitda"] == round((20 - 60) / 70, 2)
    assert out["roe"] == 8.5 and out["roa"] == 6.8
    assert out["roce"] == round(40 / (500 - 80) * 100, 2)
    assert out["rev_3y_cagr"] == round(((300 / 200) ** (1 / 3) - 1) * 100, 2)
    assert out["tax_rate"] == 20.0
    assert out["ocf_to_pat"] == round(45 / 34, 3)
    assert out["fcf_to_pat"] == round(25 / 34, 3)  # OCF + negative capex
    assert out["inventory_days"] == round(50 / 16 * 365, 1)
    assert out["goodwill_to_equity"] == 0.025

    # Valuation inputs — EPS by fiscal year (a missing year stays missing).
    assert out["eps_history"] == {"2025-12-31": 5.0, "2024-12-31": 3.7, "2022-12-31": 4.1}
    assert (out["net_income"], out["shareholders_equity"], out["free_cash_flow"]) == (34.0, 400.0, 25.0)

    assert out["quarterly_dates"] == ["2026-09-30", "2026-06-30", "2025-09-30"]
    assert out["latest_yoy_revenue_growth"] == round((86 - 79) / 79 * 100, 2)

    assert out["currency"] == out["financial_currency"] == "KRW"
    assert out["exchange"] == "KSE"
    assert out["market_cap"] == 3.4e14
    assert out["insider_holding"] == 20.0 and out["institutional_holding"] == 35.0
    assert out["book_value_per_share"] == 57000.0
    assert out["analyst_opinion_count"] == 40
    assert out["full_time_employees"] == 125000


def test_history_bundle_returns_frames_prices_and_metadata(samsung):
    bundle = yfinance_adapter._fetch_history_bundle_sync("005930.KS")
    assert bundle.prices[:2] == [("2018-05-03", 1060.0), ("2018-05-04", 1020.0)]
    assert bundle.total_return[:2] == [("2018-05-03", 980.0), ("2018-05-04", 945.0)]
    assert bundle.splits == [("2018-05-04", 50.0)]  # Samsung's 50-for-1 split
    assert list(bundle.income.columns) == list(YEARS)
    assert bundle.metadata["company_name"] == "Samsung Electronics Co., Ltd."


def test_missing_history_columns_give_no_rows():
    assert yfinance_adapter._column_rows(None, "Close") == []
    assert yfinance_adapter._column_rows(pd.DataFrame({"Close": [1.0]}), "Adj Close") == []


def test_broken_ticker_degrades_to_empty(monkeypatch):
    import yfinance

    def boom(symbol: str):
        raise RuntimeError("Yahoo is down")

    monkeypatch.setattr(yfinance, "Ticker", boom)
    assert yfinance_adapter._fetch_yfinance_sync("MSFT") == ({}, [])
    assert yfinance_adapter._fetch_history_bundle_sync("MSFT").prices == []


def test_failing_accessors_are_skipped(monkeypatch):
    import yfinance

    class Flaky(FakeSamsung):
        @property
        def balance_sheet(self):  # type: ignore[override]
            raise ConnectionError("reset by peer")

        @property
        def info(self):  # type: ignore[override]
            raise ConnectionError("rate limited")

    monkeypatch.setattr(yfinance, "Ticker", Flaky)
    out, prices = yfinance_adapter._fetch_yfinance_sync("005930.KS")
    assert prices and "debt_to_equity" not in out and "currency" not in out
    assert out["interest_coverage"] == 50.0


def test_cagr_skips_non_consecutive_years():
    """A missing fiscal year must not stretch a '3-year' CAGR to four years."""
    gappy = pd.DataFrame(
        {pd.Timestamp("2025-12-31"): [300.0], pd.Timestamp("2023-12-31"): [260.0],
         pd.Timestamp("2022-12-31"): [230.0], pd.Timestamp("2021-12-31"): [200.0]},
        index=["Total Revenue"],
    )
    assert yfinance_adapter._compute_cagr(gappy, ("Total Revenue",), years=3) is None
