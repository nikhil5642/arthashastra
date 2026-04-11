"""Fundamentals source tests — mock both the yfinance and screener.in adapters.

No network access. No fixtures. Tests either construct BalanceSheetSnapshot
directly for pure-logic checks, or monkeypatch `_fetch_yfinance_sync` and
`_fetch_screener_in` to return canned dicts for merge/fetch checks.
"""

from __future__ import annotations

from data_sources.fundamentals_source import (
    BalanceSheetSnapshot,
    FetchResult,
    FundamentalsSource,
    SCORED_FIELDS,
    _apply_balance_sheet,
    _apply_profit_loss,
    _drop_ttm,
    _latest,
    _merge,
    _parse_float,
    _parse_pct,
    _screener_cagr,
    _source_tag,
)
from data_sources.fundamentals_source import (
    screener_adapter,
    yfinance_adapter,
)


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
    sc = {"roe": 8.4, "roce": 9.7, "promoter_holding": 50.0}
    merged = _merge(yf, sc)
    assert merged["roe"] == 8.4  # screener wins
    assert merged["roce"] == 9.7  # screener wins
    assert merged["debt_to_equity"] == 0.5  # yfinance only
    assert merged["promoter_holding"] == 50.0  # screener only


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
            "promoter_holding": 48.0,
            "pledge_pct": 0.0,
            "market_cap_cr": 10000.0,
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
    assert snap.promoter_holding == 48.0
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
        return {"roe": 15.0, "roce": 18.0, "promoter_holding": 50.0}

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
            "net profit": [80.0, 100.0, 130.0, 160.0, 180.0],
            "tax %": [25.0, 25.0, 25.0, 25.0, 25.0],
        },
    )
    # CAGR uses Mar-22 (1000) → Mar-25 (1800): (1800/1000)^(1/3) - 1 = 21.64%.
    assert out["rev_3y_cagr"] == 21.64
    # Latest margins come from TTM row.
    assert out["operating_margin"] == 13.0  # 260 / 2000
    assert out["interest_coverage"] == 13.0  # 260 / 20
    assert out["net_margin"] == 9.0  # 180 / 2000


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
