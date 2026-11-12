"""Point-in-time snapshot tests — nothing that wasn't public may leak in."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data_sources.fundamentals_source.history import (
    AnnualFrames,
    StatementHistory,
    rebase_sec_eps,
    snapshot_as_of,
)
from data_sources.fundamentals_source.sec_adapter import SecAnnualFrames


def _screener_history() -> StatementHistory:
    """An Indian listing with four fiscal years (Mar year-end) plus a TTM column."""
    headers = ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025", "TTM"]
    return StatementHistory(
        symbol="RELIANCE.NS",
        screener={
            "profit-loss": (
                headers,
                {
                    "sales": [100.0, 120.0, 150.0, 200.0, 220.0],
                    "operating profit": [20.0, 24.0, 30.0, 40.0, 44.0],
                    "depreciation": [5.0, 5.0, 6.0, 7.0, 7.0],
                    "interest": [2.0, 2.0, 2.0, 2.0, 2.0],
                    "net profit": [10.0, 12.0, 15.0, 20.0, 22.0],
                },
            ),
            "balance-sheet": (
                headers[:-1],
                {
                    "equity capital": [10.0, 10.0, 10.0, 10.0],
                    "reserves": [90.0, 100.0, 110.0, 130.0],
                    "borrowings": [20.0, 20.0, 25.0, 30.0],
                    "total assets": [150.0, 160.0, 180.0, 210.0],
                },
            ),
        },
        metadata={"sector": "Energy", "currency": "INR", "financial_currency": "INR"},
    )


def test_nothing_is_public_before_the_first_reporting_lag():
    assert snapshot_as_of(_screener_history(), date(2022, 5, 31)) is None


def test_a_fiscal_year_appears_only_after_the_lag():
    history = _screener_history()
    before = snapshot_as_of(history, date(2025, 6, 28))  # Mar-25 + 89 days
    after = snapshot_as_of(history, date(2025, 6, 30))  # Mar-25 + 91 days
    assert before is not None and after is not None
    assert before.latest_annual_period == "2024-03-31"
    assert after.latest_annual_period == "2025-03-31"


def test_snapshot_never_uses_ttm_or_future_columns():
    snap = snapshot_as_of(_screener_history(), date(2024, 12, 31))
    assert snap is not None
    assert snap.net_margin == 10.0  # FY24: 15 / 150 — not TTM's 22 / 220
    assert snap.roe == round(15 / 120 * 100, 2)  # FY24 profit / FY24 equity
    assert snap.debt_to_equity == round(25 / 120, 3)
    assert snap.currency == "INR"  # identity copied from today's metadata


def test_cagr_needs_four_visible_years():
    history = _screener_history()
    assert snapshot_as_of(history, date(2024, 12, 31)).rev_3y_cagr is None
    assert snapshot_as_of(history, date(2025, 12, 31)).rev_3y_cagr == 25.99


def _frames_history() -> StatementHistory:
    """A US filer in yfinance / SEC frame shape, newest column first."""
    cols = pd.to_datetime(["2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"])
    income = pd.DataFrame(
        [[280.0, 245.0, 211.0, 198.0], [100.0, 88.0, 72.0, 72.0], [120.0, 109.0, 88.0, 83.0]],
        index=["Total Revenue", "Net Income", "Operating Income"],
        columns=cols,
    )
    balance = pd.DataFrame(
        [[600.0, 510.0, 410.0, 360.0], [300.0, 260.0, 200.0, 170.0]],
        index=["Total Assets", "Stockholders Equity"],
        columns=cols,
    )
    return StatementHistory(
        symbol="MSFT",
        frames=AnnualFrames(income=income, balance=balance, cashflow=None, source="sec"),
        metadata={"sector": "Technology", "currency": "USD"},
    )


def test_frames_are_truncated_to_public_fiscal_years():
    snap = snapshot_as_of(_frames_history(), date(2024, 12, 31))
    assert snap is not None
    assert snap.latest_annual_period == "2024-06-30"
    assert snap.roe == round(88 / 260 * 100, 2)
    # Only three years visible: growth falls back to two years, and says so.
    assert (snap.rev_3y_cagr, snap.growth_years) == (11.24, 2)


def test_deep_frames_give_cagr_once_four_years_are_public():
    snap = snapshot_as_of(_frames_history(), date(2025, 12, 31))
    assert snap is not None
    assert snap.rev_3y_cagr == round(((280 / 198) ** (1 / 3) - 1) * 100, 2)


def test_fundamentals_source_label():
    assert _frames_history().fundamentals_source == "sec"
    assert _screener_history().fundamentals_source == "screener"
    assert StatementHistory(symbol="X").fundamentals_source == "none"


# ── Per-share basis ───────────────────────────────────────────────────────────


def _apple_sec_frames() -> SecAnnualFrames:
    """Apple's first-filed EPS: FY2013 before both splits, FY2020 after the 4:1."""
    cols = pd.to_datetime(["2020-09-26", "2016-09-24", "2013-09-28"])
    income = pd.DataFrame(
        [[57_411.0, 45_687.0, 37_037.0], [3.28, float("nan"), 39.75]],
        index=["Net Income", "Diluted EPS"],
        columns=cols,
    )
    filed = {date(2020, 9, 26): date(2020, 10, 30), date(2013, 9, 28): date(2013, 10, 30)}
    return SecAnnualFrames(income=income, balance=income.iloc[:0], cashflow=income.iloc[:0], eps_filed=filed)


def test_sec_eps_is_rebased_by_every_split_after_its_filing():
    frames = _apple_sec_frames()
    rebase_sec_eps(frames, [("2014-06-09", 7.0), ("2020-08-31", 4.0)])
    eps = frames.income.loc["Diluted EPS"]
    assert eps.iloc[0] == 3.28  # filed after the 2020 split: already on today's basis
    assert eps.iloc[2] == pytest.approx(39.75 / 28)  # 7 × 4
    assert pd.isna(eps.iloc[1])  # missing stays missing


def test_rebase_without_an_eps_row_or_splits_changes_nothing():
    frames = _apple_sec_frames()
    rebase_sec_eps(frames, [])
    assert list(frames.income.loc["Diluted EPS"].dropna()) == [3.28, 39.75]
    no_eps = SecAnnualFrames(
        income=frames.income.drop("Diluted EPS"), balance=frames.balance, cashflow=frames.cashflow
    )
    rebase_sec_eps(no_eps, [("2014-06-09", 7.0)])
    assert "Diluted EPS" not in no_eps.income.index


def test_snapshot_is_priced_on_its_as_of_date():
    history = _frames_history()
    history.metadata = {**history.metadata, "currency": "USD", "financial_currency": "USD"}
    history.frames.income.loc["Diluted EPS"] = [10.0, 8.8, 7.2, 7.2]
    history.prices = [("2025-06-30", 250.0), ("2025-09-30", 300.0)]
    snap = snapshot_as_of(history, date(2025, 10, 1))
    assert snap is not None
    assert snap.current_price == 300.0
    assert snap.pe_ratio == 30.0  # 300 / FY25 EPS 10
    assert snap.pe_history == [25.0]  # FY25 year-end close 250 / 10; earlier years have no price


# ── Fetch orchestration ───────────────────────────────────────────────────────


async def test_fetch_prefers_sec_frames_and_keeps_yahoo_prices(monkeypatch):
    from data_sources.fundamentals_source import history, screener_adapter, sec_adapter, yfinance_adapter

    yahoo = yfinance_adapter.YahooHistory(
        income=_frames_history().frames.income, balance=None, cashflow=None,
        prices=[("1986-03-13", 0.06)], total_return=[("1986-03-13", 0.04)],
        splits=[("2014-06-09", 7.0), ("2020-08-31", 4.0)],
        metadata={"company_name": "Microsoft Corporation"},
    )
    sec_frames = _apple_sec_frames()

    async def fake_yahoo(symbol: str):
        return yahoo

    async def fake_sec(symbol: str, *, user_agent: str):
        assert user_agent == "ua"
        return sec_frames

    async def no_tables(symbol: str):
        return {}

    monkeypatch.setattr(yfinance_adapter, "fetch_history_bundle", fake_yahoo)
    monkeypatch.setattr(sec_adapter, "fetch_annual_frames", fake_sec)
    monkeypatch.setattr(screener_adapter, "fetch_tables", no_tables)

    fetched = await history.fetch_statement_history("msft", sec_user_agent="ua")
    assert fetched.symbol == "MSFT"
    assert fetched.frames is not None and fetched.frames.source == "sec"
    assert fetched.frames.income.loc["Diluted EPS"].iloc[2] == pytest.approx(39.75 / 28)  # rebased
    assert fetched.prices == [("1986-03-13", 0.06)]
    assert fetched.total_return == [("1986-03-13", 0.04)]
    assert fetched.metadata["company_name"] == "Microsoft Corporation"


async def test_fetch_survives_every_source_failing(monkeypatch):
    from data_sources.fundamentals_source import history, screener_adapter, sec_adapter, yfinance_adapter

    async def boom(*args, **kwargs):
        raise RuntimeError("source down")

    monkeypatch.setattr(yfinance_adapter, "fetch_history_bundle", boom)
    monkeypatch.setattr(sec_adapter, "fetch_annual_frames", boom)
    monkeypatch.setattr(screener_adapter, "fetch_tables", boom)

    fetched = await history.fetch_statement_history("005930.KS", sec_user_agent="ua")
    assert (fetched.prices, fetched.frames, fetched.screener) == ([], None, {})
    assert fetched.fundamentals_source == "none"


def test_past_snapshots_carry_margin_history_and_year_on_year_balance_changes():
    """What the peak-margin guard and the early signals need, point-in-time."""
    cols = pd.to_datetime(["2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"])
    income = pd.DataFrame(
        [[280.0, 245.0, 211.0, 198.0], [98.0, 61.0, 21.0, 40.0], [10.0, 6.0, 2.0, 4.0]],
        index=["Total Revenue", "Net Income", "Diluted EPS"], columns=cols,
    )
    balance = pd.DataFrame(
        [[600.0, 510.0, 410.0, 360.0], [300.0, 260.0, 200.0, 170.0], [120.0, 60.0, 50.0, 40.0],
         [10.0, 10.0, 10.0, 10.0]],
        index=["Total Assets", "Stockholders Equity", "Total Debt", "Ordinary Shares Number"],
        columns=cols,
    )
    history = StatementHistory(
        symbol="MSFT",
        frames=AnnualFrames(income=income, balance=balance, cashflow=None, source="sec"),
        metadata={"currency": "USD", "financial_currency": "USD"},
        splits=[("2024-12-01", 2.0)],
    )

    mid = snapshot_as_of(history, date(2024, 12, 31))
    assert mid is not None
    # Only fiscal years public by then: 2022 → 2024.
    assert mid.net_margin_annual_trend == [round(40 / 198 * 100, 2), round(21 / 211 * 100, 2),
                                           round(61 / 245 * 100, 2)]
    assert mid.balance_sheet_date == "2024-06-30"
    assert mid.debt_change_1y_pct == 20.0
    assert mid.share_count_change_1y_pct == 0.0  # the split came after this year-end
    assert mid.ttm_period_end == ""  # nothing quarterly is replayed

    latest = snapshot_as_of(history, date(2025, 12, 31))
    assert latest is not None
    assert latest.debt_change_1y_pct == 100.0
    assert latest.latest_debt_to_equity == 0.4
    # A split inside the year to 2025-06-30 makes the share counts incomparable.
    assert latest.share_count_change_1y_pct is None
