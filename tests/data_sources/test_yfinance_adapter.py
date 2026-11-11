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


# ── The quarters since the fiscal year: trailing twelve months, trends, changes ──

Q = pd.to_datetime(["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])


class FakeAlphabet(FakeSamsung):
    """A US filer with five clean quarters, paper gains in its profit, and a debt-funded build-out."""

    income_stmt = pd.DataFrame(
        {
            YEARS[0]: [400.0, 130.0, 132.0, 112.0, 11.0],
            YEARS[1]: [350.0, 112.0, 100.0, 98.0, 8.0],
            YEARS[2]: [307.0, 84.0, 74.0, 76.0, 5.8],
            YEARS[3]: [283.0, 75.0, 60.0, 65.0, 4.6],
        },
        index=["Total Revenue", "Operating Income", "Net Income", "Normalized Income", "Diluted EPS"],
    )
    balance_sheet = pd.DataFrame(
        {YEARS[0]: [595.0, 103.0, 415.0, 59.0], YEARS[1]: [450.0, 89.0, 325.0, 23.0]},
        index=["Total Assets", "Current Liabilities", "Stockholders Equity", "Total Debt"],
    )
    cashflow = pd.DataFrame(
        {YEARS[0]: [165.0, -92.0, 25.0]},
        index=["Operating Cash Flow", "Capital Expenditure", "Stock Based Compensation"],
    )
    quarterly_income_stmt = pd.DataFrame(
        {
            Q[0]: [128.0, 41.0, 26.0, 34.0],
            Q[1]: [120.0, 41.0, 112.0, 32.0],
            Q[2]: [110.0, 40.0, 63.0, 33.0],
            Q[3]: [114.0, 36.0, 34.0, 32.0],
            Q[4]: [102.0, 31.0, 35.0, 26.0],
        },
        index=["Total Revenue", "Operating Income", "Net Income", "Normalized Income"],
    )
    quarterly_cashflow = pd.DataFrame(
        {Q[0]: [42.0, -51.0, 8.6], Q[1]: [39.0, -45.0, 8.0], Q[2]: [46.0, -36.0, 6.8],
         Q[3]: [52.0, -28.0, 7.0], Q[4]: [48.0, -24.0, 6.4]},
        index=["Operating Cash Flow", "Capital Expenditure", "Stock Based Compensation"],
    )
    quarterly_balance_sheet = pd.DataFrame(
        {
            Q[0]: [1003.0, 678.0, 142.0, 73.0, 67.0, 12.29],
            Q[1]: [922.0, 640.0, 113.0, 69.0, 67.0, 12.23],
            Q[2]: [704.0, 479.0, 90.0, 63.0, 67.0, 12.12],
            Q[3]: [595.0, 415.0, 59.0, 63.0, 33.0, 12.09],
            Q[4]: [536.0, 387.0, 34.0, 57.0, 33.0, 12.08],
        },
        index=["Total Assets", "Stockholders Equity", "Total Debt", "Accounts Receivable",
               "Goodwill And Other Intangible Assets", "Ordinary Shares Number"],
    )


@pytest.fixture
def alphabet(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", FakeAlphabet)


def test_trailing_twelve_months_cover_the_four_newest_quarters(alphabet):
    out, _ = yfinance_adapter._fetch_yfinance_sync("GOOG")

    assert out["ttm_period_end"] == "2026-09-30"
    assert out["ttm_revenue"] == 128 + 120 + 110 + 114
    assert out["ttm_net_income"] == 26 + 112 + 63 + 34
    assert out["ttm_operating_cash_flow"] == 42 + 39 + 46 + 52
    assert out["ttm_capex"] == 51 + 45 + 36 + 28  # outflows, stored positive
    assert out["ttm_free_cash_flow"] == (42 + 39 + 46 + 52) - (51 + 45 + 36 + 28)
    assert out["ttm_stock_based_compensation"] == pytest.approx(8.6 + 8.0 + 6.8 + 7.0)
    # The fiscal year stays what it was — the scored ratios are annual.
    assert (out["operating_cash_flow"], out["capex"], out["free_cash_flow"]) == (165.0, 92.0, 73.0)
    assert out["revenue"] == 400.0 and out["stock_based_compensation"] == 25.0


def test_core_earnings_ratio_is_normalized_over_reported_profit(alphabet):
    out, _ = yfinance_adapter._fetch_yfinance_sync("GOOG")
    assert out["core_earnings_ratio_fy"] == round(112 / 132, 3)
    assert out["core_earnings_ratio_ttm"] == round((34 + 32 + 33 + 32) / (26 + 112 + 63 + 34), 3)
    # A loss-making period has no ratio: there is no profit to be a share of.
    assert yfinance_adapter._core_earnings_ratio(5.0, -10.0) is None
    assert yfinance_adapter._core_earnings_ratio(None, 10.0) is None


def test_balance_sheet_changes_compare_the_latest_quarter_with_a_year_earlier(alphabet):
    out, _ = yfinance_adapter._fetch_yfinance_sync("GOOG")
    assert out["balance_sheet_date"] == "2026-09-30"
    assert out["debt_change_1y_pct"] == round((142 - 34) / 34 * 100, 1)
    assert out["latest_debt_to_equity"] == round(142 / 678, 3)
    assert out["debt_to_equity_1y_ago"] == round(34 / 387, 3)
    assert out["receivables_change_1y_pct"] == round((73 - 57) / 57 * 100, 1)
    assert out["goodwill_change_1y_pct"] == round((67 - 33) / 33 * 100, 1)
    assert out["latest_goodwill_to_equity"] == round(67 / 678, 3)
    assert out["share_count_change_1y_pct"] == round((12.29 - 12.08) / 12.08 * 100, 1)
    assert "inventory_change_1y_pct" not in out  # no inventory row: unknown, not zero


def test_a_split_inside_the_year_leaves_the_share_count_change_unknown(monkeypatch):
    import yfinance

    class SplitAlphabet(FakeAlphabet):
        def history(self, period: str, auto_adjust: bool) -> pd.DataFrame:
            days = pd.to_datetime(["2026-07-15", "2026-11-05"])
            return pd.DataFrame(
                {"Close": [150.0, 285.0], "Adj Close": [150.0, 285.0], "High": [151.0, 286.0],
                 "Low": [149.0, 284.0], "Stock Splits": [20.0, 0.0]},
                index=days,
            )

    monkeypatch.setattr(yfinance, "Ticker", SplitAlphabet)
    out, _ = yfinance_adapter._fetch_yfinance_sync("GOOG")
    assert "share_count_change_1y_pct" not in out
    assert out["debt_change_1y_pct"] > 0  # everything else is still compared


def test_fiscal_year_trends_and_quarterly_margins(alphabet):
    out, _ = yfinance_adapter._fetch_yfinance_sync("GOOG")
    # Oldest → newest.
    assert out["sales_annual_trend"] == [283.0, 307.0, 350.0, 400.0]
    assert out["net_margin_annual_trend"] == [
        round(60 / 283 * 100, 2), round(74 / 307 * 100, 2), round(100 / 350 * 100, 2), 33.0
    ]
    assert out["operating_margin_annual_trend"][-1] == 32.5
    # ROCE needs a balance sheet for the same fiscal year-end: only two years have one.
    assert out["roce_annual_trend"] == [round(112 / (450 - 89) * 100, 2), round(130 / (595 - 103) * 100, 2)]
    # Newest first, aligned with quarterly_dates.
    assert out["quarterly_dates"][0] == "2026-09-30"
    assert out["quarterly_operating_margin"] == [
        round(41 / 128 * 100, 2), round(41 / 120 * 100, 2), round(40 / 110 * 100, 2),
        round(36 / 114 * 100, 2), round(31 / 102 * 100, 2),
    ]


def test_half_yearly_reporters_get_no_trailing_window(samsung):
    # FakeSamsung's quarterly frame holds three scattered quarters.
    out, _ = yfinance_adapter._fetch_yfinance_sync("005930.KS")
    assert "ttm_period_end" not in out and "ttm_revenue" not in out
    assert out["quarterly_operating_margin"] == [None, None, None]  # no operating line per quarter


def test_cash_flow_of_another_window_is_not_mixed_in(monkeypatch):
    import yfinance

    class LaggingCashFlow(FakeAlphabet):
        quarterly_cashflow = FakeAlphabet.quarterly_cashflow.drop(columns=[Q[0]])

    monkeypatch.setattr(yfinance, "Ticker", LaggingCashFlow)
    out, _ = yfinance_adapter._fetch_yfinance_sync("GOOG")
    assert out["ttm_revenue"] == 472.0  # the income window still stands
    assert "ttm_free_cash_flow" not in out and "ttm_capex" not in out


def test_profit_that_turned_into_a_loss_counts_as_minus_100_growth():
    df = pd.DataFrame(
        {YEARS[0]: [-12.0, 300.0], YEARS[1]: [5.0, 280.0], YEARS[2]: [20.0, 260.0], YEARS[3]: [40.0, 250.0]},
        index=["Net Income", "Total Revenue"],
    )
    assert yfinance_adapter._compute_cagr(df, ("Net Income",), years=3, wiped_out_counts=True) == -100.0
    assert yfinance_adapter._compute_cagr(df, ("Net Income",), years=3) is None


def test_ebit_is_not_a_stand_in_for_operating_income():
    """Yahoo's EBIT carries other income and one-off gains — coverage must not use it."""
    is_ = pd.DataFrame({YEARS[0]: [160.0, 2.0, 400.0]}, index=["EBIT", "Interest Expense", "Total Revenue"])
    out = yfinance_adapter.SnapshotFields()
    yfinance_adapter._fill_income_statement_derived(out, is_, None)
    assert "interest_coverage" not in out and "operating_margin" not in out
