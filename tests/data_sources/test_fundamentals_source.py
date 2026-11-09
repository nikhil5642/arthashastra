"""Fundamentals source tests — mock both the yfinance and screener.in adapters.

No network access. No fixtures. Tests either construct BalanceSheetSnapshot
directly for pure-logic checks, or monkeypatch `_fetch_yfinance_sync` and
`_fetch_screener_in` to return canned dicts for merge/fetch checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from data_sources.fundamentals_source import (
    SCORED_FIELDS,
    BalanceSheetSnapshot,
    FetchResult,
    FundamentalsSource,
    latest_yoy_growth,
    pct_change_by_date,
    screener_adapter,
    yfinance_adapter,
)
from data_sources.fundamentals_source.primitives import (
    _drop_ttm,
    _latest,
    _latest_aligned,
    _parse_float,
    _parse_pct,
    _screener_cagr,
    _value_at_label,
)
from data_sources.fundamentals_source.screener_adapter import (
    _apply_asset_quality,
    _apply_balance_sheet,
    _apply_cash_flow,
    _apply_derived,
    _apply_profit_loss,
    _apply_quarters,
)
from data_sources.fundamentals_source.source import _drop_non_finite, _merge, _source_tag

if TYPE_CHECKING:
    from selectolax.parser import HTMLParser


def test_balance_sheet_snapshot_defaults():
    snap = BalanceSheetSnapshot(ticker="X", as_of_date="2026-04-11")
    assert snap.data_completeness_pct == 0.0
    assert snap.to_dict()["ticker"] == "X"


def test_data_completeness_pct_counts_populated_fields():
    snap = BalanceSheetSnapshot(
        ticker="X",
        as_of_date="2026-04-11",
        debt_to_equity=0.5,
        roe=15.0,
        roce=18.0,
    )
    expected = round(100 * 3 / len(SCORED_FIELDS), 1)
    assert snap.data_completeness_pct == expected


def test_from_dict_filters_unknown_keys():
    snap = BalanceSheetSnapshot.from_dict(
        {"ticker": "X", "roe": 10.0, "ignored_noise": "skip me"}
    )
    assert snap.roe == 10.0
    assert "ignored_noise" not in snap.to_dict()


def test_parse_pct():
    assert _parse_pct("10.5%") == 10.5
    assert _parse_pct(" 8.40 ") == 8.40
    assert _parse_pct("1,200.00") == 1200.00
    assert _parse_pct(None) is None
    assert _parse_pct("not a number") is None


def test_parse_float_handles_commas():
    assert _parse_float("1,827,979") == 1827979.0
    assert _parse_float("1350") == 1350.0
    assert _parse_float(None) is None


def test_merge_prefers_screener_for_roe_roce():
    yf = {"roe": 10.0, "roce": 12.0, "debt_to_equity": 0.5}
    sc = {"roe": 8.4, "roce": 9.7, "insider_holding": 50.0}
    merged = _merge(yf, sc)
    assert merged["roe"] == 8.4  # screener wins
    assert merged["roce"] == 9.7  # screener wins
    assert merged["debt_to_equity"] == 0.5  # yfinance only
    assert merged["insider_holding"] == 50.0  # screener only


def test_source_tag_variants():
    assert _source_tag({"a": 1}, {"b": 2}) == "yfinance+screener"
    assert _source_tag({"a": 1}, {}) == "yfinance"
    assert _source_tag({}, {"b": 2}) == "screener"
    assert _source_tag({}, {}) == "empty"


async def test_fetch_merges_mocked_sources(monkeypatch):
    def fake_yf_sync(ticker: str):
        return (
            {
                "debt_to_equity": 0.4,
                "interest_coverage": 10.5,
                "current_ratio": 1.7,
                "quick_ratio": 1.2,
                "net_debt_to_ebitda": 1.2,
                "roa": 10.0,
                "rev_3y_cagr": 12.0,
                "pat_3y_cagr": 9.0,
                "ocf_to_pat": 1.1,
                "fcf_to_pat": 0.6,
                "current_price": 1500.0,
                "high_52w": 1800.0,
                "low_52w": 1200.0,
                "company_name": "Test Industries Limited",
                "sector": "Energy",
                "industry": "Oil & Gas",
            },
            [("2026-04-01", 1600.0), ("2026-04-02", 1500.0)],
        )

    async def fake_screener(ticker: str) -> dict:
        return {
            "roe": 18.5,
            "roce": 22.0,
            "insider_holding": 48.0,
            "pledge_pct": 0.0,
            "market_cap": 10000.0,
            "company_name": "Test Industries Limited (Consolidated)",
        }

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", fake_yf_sync)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", fake_screener)

    result = await FundamentalsSource().fetch_full("TEST")
    assert isinstance(result, FetchResult)
    snap = result.snapshot
    assert snap.ticker == "TEST"
    assert snap.roe == 18.5  # screener wins
    assert snap.debt_to_equity == 0.4  # yfinance only
    assert snap.current_price == 1500.0
    assert snap.insider_holding == 48.0
    assert snap.source == "yfinance+screener"
    assert snap.data_completeness_pct == 100.0
    assert snap.field_sources["roe"] == "screener"
    assert snap.field_sources["debt_to_equity"] == "yfinance"
    assert result.price_history == [("2026-04-01", 1600.0), ("2026-04-02", 1500.0)]


async def test_fetch_returns_snapshot_only_via_convenience(monkeypatch):
    def fake_yf_sync(ticker: str):
        return ({"debt_to_equity": 0.5, "current_price": 100.0}, [])

    async def fake_screener(ticker: str) -> dict:
        return {}

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", fake_yf_sync)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", fake_screener)

    snap = await FundamentalsSource().fetch("TEST")
    assert isinstance(snap, BalanceSheetSnapshot)
    assert snap.debt_to_equity == 0.5


async def test_fetch_handles_yfinance_exception(monkeypatch):
    def boom(ticker: str):
        raise RuntimeError("rate limited")

    async def fake_screener(ticker: str) -> dict:
        return {"roe": 15.0, "roce": 18.0, "insider_holding": 50.0}

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", boom)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", fake_screener)

    snap = await FundamentalsSource().fetch("TEST")
    assert snap.source == "screener"
    assert snap.roe == 15.0
    assert snap.debt_to_equity is None


async def test_fetch_handles_screener_exception(monkeypatch):
    def fake_yf_sync(ticker: str):
        return ({"debt_to_equity": 0.5, "current_price": 100.0}, [])

    async def boom(ticker: str) -> dict:
        raise RuntimeError("screener blocked")

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", fake_yf_sync)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", boom)

    snap = await FundamentalsSource().fetch("TEST")
    assert snap.source == "yfinance"
    assert snap.debt_to_equity == 0.5


async def test_fetch_empty_when_both_sources_fail(monkeypatch):
    def boom_sync(ticker: str):
        raise RuntimeError("x")

    async def boom_async(ticker: str) -> dict:
        raise RuntimeError("y")

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", boom_sync)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", boom_async)

    result = await FundamentalsSource().fetch_full("NOPE")
    assert result.snapshot.source == "empty"
    assert result.snapshot.data_completeness_pct == 0.0
    assert result.price_history == []


# ── Screener section helpers ──────────────────────────────────────────────────


def test_drop_ttm_trims_trailing_ttm_column():
    headers = ["Mar 2023", "Mar 2024", "Mar 2025", "TTM"]
    values = [100.0, 150.0, 200.0, 220.0]
    assert _drop_ttm(headers, values) == [100.0, 150.0, 200.0]


def test_drop_ttm_leaves_annual_only_lists_alone():
    headers = ["Mar 2023", "Mar 2024", "Mar 2025"]
    values = [100.0, 150.0, 200.0]
    assert _drop_ttm(headers, values) == [100.0, 150.0, 200.0]


def test_drop_ttm_handles_empty_inputs():
    assert _drop_ttm([], []) == []
    assert _drop_ttm(["Mar 2025", "TTM"], None) == []


def test_latest_skips_trailing_nones():
    assert _latest([1.0, 2.0, None]) == 2.0
    assert _latest([None, None]) is None
    assert _latest(None) is None


def test_screener_cagr_computes_from_endpoints():
    # 100 → 200 over 3 years ⇒ ~25.99% CAGR.
    values = [100.0, 120.0, 150.0, 200.0]
    assert _screener_cagr(values, years=3) == 25.99


def test_screener_cagr_returns_none_for_sparse_data():
    assert _screener_cagr([100.0, 200.0], years=3) is None
    assert _screener_cagr(None, years=3) is None
    # Endpoint missing ⇒ can't compute.
    assert _screener_cagr([None, 1.0, 2.0, 3.0], years=3) is None
    assert _screener_cagr([1.0, 2.0, 3.0, None], years=3) is None


def test_screener_cagr_skips_non_positive_endpoints():
    # Loss-turning-to-profit: past PAT was 0 → CAGR undefined.
    assert _screener_cagr([0.0, 50.0, 100.0, 150.0], years=3) is None
    assert _screener_cagr([-10.0, 50.0, 100.0, 150.0], years=3) is None


def test_apply_balance_sheet_accepts_banker_borrowing_label():
    """HDFC Bank / PSU banks label the row 'Borrowing', not 'Borrowings+'."""
    out: dict = {}
    _apply_balance_sheet(
        out,
        headers=["Mar 2023", "Mar 2024", "Mar 2025", "Sep 2025"],
        rows={
            "equity capital": [100.0, 100.0, 200.0, 200.0],
            "reserves": [800.0, 900.0, 1200.0, 1300.0],
            "borrowing": [500.0, 600.0, 700.0, 750.0],
        },
    )
    # D/E = 750 / (200 + 1300) = 0.5
    assert out["debt_to_equity"] == 0.5


def test_apply_profit_loss_drops_ttm_before_cagr_but_uses_ttm_for_margins():
    """The TTM column should feed 'latest margin' but NOT the 3y CAGR window."""
    out: dict = {}
    headers = ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025", "TTM"]
    _apply_profit_loss(
        out,
        headers=headers,
        rows={
            "sales": [1000.0, 1200.0, 1500.0, 1800.0, 2000.0],
            "operating profit": [100.0, 130.0, 170.0, 220.0, 260.0],
            "interest": [10.0, 12.0, 14.0, 18.0, 20.0],
            "depreciation": [20.0, 25.0, 30.0, 35.0, 40.0],
            "other income": [5.0, 5.0, 5.0, 5.0, 900.0],  # one-off gain in TTM
            "net profit": [80.0, 100.0, 130.0, 160.0, 180.0],
            "tax %": [25.0, 25.0, 25.0, 25.0, 25.0],
        },
    )
    # CAGR uses Mar-22 (1000) → Mar-25 (1800): (1800/1000)^(1/3) - 1 = 21.64%.
    assert out["rev_3y_cagr"] == 21.64
    # Latest margins come from TTM row.
    assert out["operating_margin"] == 13.0  # 260 / 2000
    assert out["net_margin"] == 9.0  # 180 / 2000
    # Coverage uses core EBIT — (operating profit 260 − depreciation 40) / 20 —
    # so the one-off "other income" can't inflate it.
    assert out["interest_coverage"] == 11.0
    # The latest *annual* column, not TTM, names the fiscal year.
    assert out["latest_annual_period"] == "2025-03-31"


def test_apply_profit_loss_no_ttm_uses_all_annual_points():
    """Young companies without a TTM column compute CAGR over raw annuals."""
    out: dict = {}
    headers = ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025"]
    _apply_profit_loss(
        out,
        headers=headers,
        rows={
            "sales": [100.0, 200.0, 400.0, 800.0],
            "net profit": [10.0, 20.0, 40.0, 80.0],
        },
    )
    # 100 → 800 over 3 years ⇒ 100% CAGR.
    assert out["rev_3y_cagr"] == 100.0
    assert out["pat_3y_cagr"] == 100.0


# ── Reliability fixes (2026-11 audit) ─────────────────────────────────────────


def _html(body: str) -> "HTMLParser":
    from selectolax.parser import HTMLParser

    return HTMLParser(f"<html><body>{body}</body></html>")


def test_shareholding_without_pledge_row_leaves_pledge_unknown():
    """Screener has no pledge row; absence must stay None, never become 0%."""
    tree = _html(
        '<section id="shareholding"><div id="quarterly-shp"><table>'
        "<thead><tr><th></th><th>Jun 2026</th><th>Sep 2026</th></tr></thead>"
        "<tbody><tr><td>Promoters +</td><td>50.10%</td><td>50.20%</td></tr>"
        "<tr><td>FIIs +</td><td>20.00%</td><td>19.50%</td></tr></tbody>"
        "</table></div></section>"
    )
    out: dict = {}
    screener_adapter._parse_shareholding(tree, out)
    assert out["insider_holding"] == 50.2
    assert "pledge_pct" not in out


def test_top_ratios_high_low_reads_both_number_spans():
    tree = _html(
        '<ul id="top-ratios"><li><span class="name">High / Low</span>'
        '<span class="value">₹ <span class="number">1,612</span> / '
        '<span class="number">1,152</span></span></li></ul>'
    )
    out: dict = {}
    screener_adapter._parse_top_ratios(tree, out)
    assert out["high_52w"] == 1612.0
    assert out["low_52w"] == 1152.0


def test_parsers_treat_nan_and_inf_text_as_missing():
    assert _parse_float("nan") is None
    assert _parse_pct("inf%") is None


def test_drop_non_finite_removes_nan_and_inf():
    clean = _drop_non_finite(
        {"roe": float("nan"), "roa": float("inf"), "roce": 12.0}, label="test"
    )
    assert clean == {"roce": 12.0}


async def test_fetch_never_lets_nan_reach_the_snapshot(monkeypatch):
    def fake_yf_sync(ticker: str):
        return ({"pat_3y_cagr": float("nan"), "roe": 15.0}, [])

    async def fake_screener(ticker: str) -> dict:
        return {}

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", fake_yf_sync)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", fake_screener)

    snap = await FundamentalsSource().fetch("TEST")
    assert snap.pat_3y_cagr is None
    assert snap.roe == 15.0


async def test_fetch_records_source_conflicts_and_full_provenance(monkeypatch):
    """The Saatvik case: yfinance's lease-inflated D/E vs screener's borrowings."""

    def fake_yf_sync(ticker: str):
        return ({"debt_to_equity": 1.49, "roe": 18.0, "sector": "Energy"}, [])

    async def fake_screener(ticker: str) -> dict:
        return {"debt_to_equity": 0.52, "roe": 18.4, "company_name": "Test Ltd"}

    monkeypatch.setattr(yfinance_adapter, "_fetch_yfinance_sync", fake_yf_sync)
    monkeypatch.setattr(screener_adapter, "_fetch_screener_in", fake_screener)

    snap = await FundamentalsSource().fetch("TEST")
    assert snap.debt_to_equity == 0.52  # screener still wins
    [conflict] = snap.data_conflicts  # ROE 18.0 vs 18.4 is within tolerance
    assert conflict.field_name == "debt_to_equity"
    assert conflict.chosen_source == "screener"
    assert conflict.other_value == 1.49
    # Provenance covers every populated field, not just the scored ones.
    assert snap.field_sources["sector"] == "yfinance"
    assert snap.field_sources["company_name"] == "screener"


def test_latest_aligned_takes_the_newest_column_shared_by_all_rows():
    # TTM interest is missing, so PBT must come from the same annual column.
    assert _latest_aligned([10.0, 20.0, 30.0], [1.0, 2.0, None]) == (20.0, 2.0)
    assert _latest_aligned([1.0], None) is None


def test_value_at_label_matches_columns_across_tables():
    assert _value_at_label(["Mar 2024", "Mar 2025"], [1.0, 2.0], "Mar 2025") == 2.0
    assert _value_at_label(["Mar 2024"], [1.0], "Mar 2025") is None


def test_cash_flow_ratio_uses_profit_of_the_same_fiscal_year():
    """P&L already shows FY26; the cash-flow table only reaches FY25."""
    out: dict = {}
    _apply_cash_flow(
        out,
        cf_headers=["Mar 2024", "Mar 2025"],
        cf_rows={"cash from operating activity": [90.0, 120.0]},
        pl_headers=["Mar 2024", "Mar 2025", "Mar 2026", "TTM"],
        pl_rows={"net profit": [80.0, 100.0, 200.0, 210.0]},
    )
    assert out["ocf_to_pat"] == 1.2  # FY25 OCF 120 / FY25 profit 100


def test_roa_divides_by_assets_at_the_same_year_end():
    """The balance sheet's newest column is a half-year; ROA must skip it."""
    out: dict = {}
    _apply_derived(
        out,
        bs_headers=["Mar 2025", "Sep 2025"],
        bs_rows={"total assets": [1000.0, 5000.0]},
        pl_headers=["Mar 2025", "TTM"],
        pl_rows={"net profit": [100.0, 130.0]},
    )
    assert out["roa"] == 10.0


def test_quarters_stay_aligned_when_one_row_has_a_hole():
    out: dict = {}
    _apply_quarters(
        out,
        headers=["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"],
        rows={
            "sales": [100.0, 110.0, 120.0, 130.0, 150.0],
            "net profit": [10.0, None, 12.0, 13.0, 15.0],
        },
    )
    # Dec 2025 has no profit, so it is dropped from all three series.
    assert out["quarterly_dates"] == ["2026-09-30", "2026-06-30", "2026-03-31", "2025-09-30"]
    assert len(out["quarterly_revenue"]) == len(out["quarterly_pat"]) == 4
    # YoY is matched by date: Sep 2026 vs Sep 2025.
    assert out["latest_yoy_revenue_growth"] == 50.0
    assert out["latest_yoy_pat_growth"] == 50.0


def test_asset_quality_reads_npa_rows_with_yoy_change():
    out: dict = {}
    _apply_asset_quality(
        out,
        {
            "gross npa %": [1.8, 1.7, 1.6, 1.5, 1.4],
            "net npa %": [0.5, 0.45, 0.42, 0.4, 0.38],
        },
    )
    assert out["gross_npa_pct"] == 1.4
    assert out["gross_npa_delta_1y_pct_points"] == -0.4
    assert out["net_npa_pct"] == 0.38
    assert out["net_npa_delta_1y_pct_points"] == -0.12


def test_borrowings_from_zero_sets_a_flag_instead_of_infinity():
    out: dict = {}
    screener_adapter._apply_multi_year_trends(
        out,
        bs_headers=["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025"],
        bs_rows={"borrowings": [0.0, 0.0, 10.0, 50.0]},
        pl_headers=[],
        pl_rows={},
        r_rows={},
    )
    assert out["borrowings_went_from_zero"] is True
    assert "borrowings_delta_3y_pct" not in out


def test_yfinance_cagr_skips_a_nan_endpoint():
    import pandas as pd

    df = pd.DataFrame(
        {"FY25": [200.0], "FY24": [150.0], "FY23": [120.0], "FY22": [float("nan")]},
        index=["Total Revenue"],
    )
    assert yfinance_adapter._compute_cagr(df, ("Total Revenue",), years=3) is None


def test_yfinance_quarterly_series_drop_misaligned_columns():
    import pandas as pd

    cols = pd.to_datetime(["2026-09-30", "2026-06-30", "2026-03-31"])
    q_is = pd.DataFrame(
        [[150.0, 130.0, 120.0], [15.0, float("nan"), 12.0]],
        index=["Total Revenue", "Net Income"],
        columns=cols,
    )
    revenue, pat, dates = yfinance_adapter._extract_quarterly_series(q_is)
    assert dates == ["2026-09-30", "2026-03-31"]
    assert revenue == [150.0, 120.0]
    assert pat == [15.0, 12.0]


def test_pct_change_by_date_ignores_position_gaps():
    values = [150.0, 120.0, 100.0]
    dates = ["2026-09-30", "2026-03-31", "2025-09-30"]
    assert latest_yoy_growth(values, dates) == 50.0
    assert pct_change_by_date(values, dates, current_idx=0, days_ago=90, tolerance=30) is None


def test_core_operating_loss_keeps_a_negative_coverage():
    """Dropping the field would hide the problem; a negative value scores 0."""
    out: dict = {}
    _apply_profit_loss(
        out,
        headers=["Mar 2026"],
        rows={
            "sales": [1000.0],
            "operating profit": [180.0],
            "depreciation": [200.0],
            "interest": [30.0],
        },
    )
    assert out["interest_coverage"] == -0.67


# ── yfinance metadata for global listings ─────────────────────────────────────


def test_listing_metadata_keeps_currency_case_and_reporting_currency():
    out: dict = {}
    yfinance_adapter._fill_listing(
        out,
        {"fullExchangeName": "LSE", "currency": "GBp", "financialCurrency": "USD",
         "marketCap": 210817236992},
    )
    assert out == {
        "exchange": "LSE",
        "currency": "GBp",  # pence — must not be upper-cased to GBP
        "financial_currency": "USD",
        "market_cap": 210817236992.0,
    }


def test_ownership_percentages_come_from_yahoo_fractions():
    out: dict = {}
    yfinance_adapter._fill_ownership(
        out, {"heldPercentInsiders": 0.0009, "heldPercentInstitutions": 0.7565}
    )
    assert out == {"insider_holding": 0.09, "institutional_holding": 75.65}


def test_book_value_is_dropped_when_currencies_differ():
    """Alibaba's ADR trades in USD but reports in CNY: P/B would be nonsense."""
    adr: dict = {}
    yfinance_adapter._fill_valuation(
        adr, {"trailingPE": 17.2, "bookValue": 432.1, "currency": "USD", "financialCurrency": "CNY"}
    )
    assert "book_value_per_share" not in adr
    assert adr["pe_ratio"] == 17.2

    samsung: dict = {}
    yfinance_adapter._fill_valuation(
        samsung,
        {"bookValue": 57000.0, "currency": "KRW", "financialCurrency": "KRW",
         "trailingAnnualDividendYield": 0.0185},
    )
    assert samsung["book_value_per_share"] == 57000.0
    assert samsung["dividend_yield"] == 1.85


def test_dividend_yield_is_dropped_when_price_and_dividend_units_differ():
    """Yahoo divides Lloyds' dividend in pounds by its price in pence: 0.04%, not 4%."""
    for financial_currency in ("GBP", "USD"):  # Lloyds reports in pounds, Shell in dollars
        london: dict = {}
        yfinance_adapter._fill_valuation(
            london,
            {"trailingPE": 9.59, "trailingAnnualDividendYield": 0.000413,
             "currency": "GBp", "financialCurrency": financial_currency},
        )
        assert "dividend_yield" not in london
        assert london["pe_ratio"] == 9.59


def test_yfinance_symbol_is_never_rewritten():
    assert yfinance_adapter._yf_symbol(" googl ") == "GOOGL"
    assert yfinance_adapter._yf_symbol("005930.ks") == "005930.KS"


async def test_screener_is_never_called_for_non_indian_listings(monkeypatch):
    async def boom(*_):
        raise AssertionError("screener must not be fetched for a US listing")

    monkeypatch.setattr(screener_adapter, "_fetch_html_tree", boom)
    assert await screener_adapter._fetch_screener_in("MSFT") == {}
    assert await screener_adapter.fetch_tables("7203.T") == {}
