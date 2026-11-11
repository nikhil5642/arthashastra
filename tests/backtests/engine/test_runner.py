"""Backtest runner tests — synthetic global histories, no network."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from backtests.engine.metrics import Observation
from backtests.engine.runner import _deterministic_score, run_backtest
from data_sources.fundamentals_source.history import AnnualFrames, StatementHistory
from scorers.base import Verdict

SHARES = 10.0


def daily_prices(
    start: date, days: int, *, start_price: float, daily_growth: float
) -> list[tuple[str, float]]:
    return [
        ((start + timedelta(days=i)).isoformat(), start_price * (1 + daily_growth) ** i)
        for i in range(days)
    ]


def us_filer(
    symbol: str, *, margin: float, total_return_growth: float | None = None
) -> StatementHistory:
    """Four fiscal years (June year-end) in SEC frame shape, priced in USD."""
    cols = pd.to_datetime(["2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"])
    revenue = [280.0, 245.0, 211.0, 198.0]
    net_income = [r * margin for r in revenue]
    income = pd.DataFrame(
        [revenue, net_income, [r * margin * 1.2 for r in revenue], [n / SHARES for n in net_income]],
        index=["Total Revenue", "Net Income", "Operating Income", "Diluted EPS"],
        columns=cols,
    )
    balance = pd.DataFrame(
        [[600.0, 510.0, 410.0, 360.0], [300.0, 260.0, 200.0, 170.0]],
        index=["Total Assets", "Stockholders Equity"],
        columns=cols,
    )
    prices = daily_prices(date(2021, 1, 1), 2200, start_price=100.0, daily_growth=0.0004)
    total_return = (
        daily_prices(date(2021, 1, 1), 2200, start_price=100.0, daily_growth=total_return_growth)
        if total_return_growth is not None
        else []
    )
    return StatementHistory(
        symbol=symbol,
        prices=prices,
        total_return=total_return,
        frames=AnnualFrames(income=income, balance=balance, cashflow=None, source="sec"),
        metadata={"sector": "Technology", "currency": "USD", "financial_currency": "USD"},
    )


def test_runner_scores_only_public_years_and_measures_forward_returns():
    histories = [us_filer("MSFT", margin=0.35), us_filer("GOOGL", margin=0.25)]
    dates = [date(2022, 6, 30), date(2023, 12, 31), date(2024, 12, 31), date(2026, 6, 30)]
    result = run_backtest(histories, dates, horizon_months=12)

    msft = [o for o in result.observations if o.ticker == "MSFT"]
    assert [o.as_of for o in msft] == dates[1:]  # nothing public on 2022-06-30
    assert msft[0].forward_return_pct == pytest.approx((1.0004 ** 366 - 1) * 100, rel=1e-3)
    assert msft[-1].forward_return_pct is None  # window runs past the price data
    assert not any(o.is_bank for o in msft)

    # Priced on each date's close against the latest public EPS.
    first = msft[0]
    assert first.valuation is not None and first.earnings_yield is not None
    assert 0 < first.completeness <= 100

    # Same returns for both tickers → nothing is above or below its date's average.
    with_returns = [o for o in result.observations if o.forward_return_pct is not None]
    assert all(o.excess_return_pct == pytest.approx(0.0) for o in with_returns)

    coverage = {c.ticker: c for c in result.coverage}
    assert coverage["MSFT"].fundamentals_source == "sec"
    assert coverage["MSFT"].scored_dates == 3
    assert coverage["MSFT"].first_scored == date(2023, 12, 31)
    assert coverage["MSFT"].price_history_start == date(2021, 1, 1)


def test_forward_returns_come_from_the_total_return_series():
    with_dividends = us_filer("KO", margin=0.2, total_return_growth=0.0006)
    [obs] = run_backtest([with_dividends], [date(2023, 12, 31)], horizon_months=12).observations
    assert obs.forward_return_pct == pytest.approx((1.0006 ** 366 - 1) * 100, rel=1e-3)


def test_dates_before_the_listing_trades_are_not_scored():
    ipo = us_filer("RBRK", margin=0.1)
    ipo.prices = [(day, close) for day, close in ipo.prices if day >= "2024-04-25"]
    result = run_backtest([ipo], [date(2023, 12, 31), date(2024, 12, 31)], horizon_months=12)
    assert [o.as_of for o in result.observations] == [date(2024, 12, 31)]
    assert result.coverage[0].first_scored == date(2024, 12, 31)


def test_ticker_without_fundamentals_is_reported_not_scored():
    empty = StatementHistory(symbol="005930.KS", prices=[("2024-01-02", 70000.0)])
    result = run_backtest([empty], [date(2025, 12, 31)], horizon_months=12)
    assert result.observations == []
    assert result.coverage[0].scored_dates == 0
    assert result.coverage[0].fundamentals_source == "none"


def test_dip_gate_quality_bar_is_the_deterministic_composite():
    observation = Observation(
        ticker="NVDA", as_of=date(2025, 12, 31), is_bank=False, fundamentals=90.0,
        completeness=80.0, valuation=60.0, forward_return_pct=None,
    )
    score = _deterministic_score(observation)
    assert score.composite == 81.0  # 0.7 × 90 + 0.3 × 60
    assert score.verdict is Verdict.STRONG_BUY
    assert score.valuation_score == 60.0 and score.data_completeness_pct == 80.0


def test_observations_record_peak_margins_and_material_early_signals():
    """A cyclical on a record margin, levering up: both show on the as-of date."""
    cols = pd.to_datetime(["2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"])
    revenue = [300.0, 150.0, 120.0, 130.0]
    profit = [180.0, 30.0, 6.0, 26.0]  # margins 60%, 20%, 5%, 20% → median 20%
    income = pd.DataFrame(
        [revenue, profit, [p / SHARES for p in profit]],
        index=["Total Revenue", "Net Income", "Diluted EPS"], columns=cols,
    )
    balance = pd.DataFrame(
        [[900.0, 500.0, 420.0, 400.0], [400.0, 250.0, 220.0, 210.0], [200.0, 60.0, 55.0, 50.0]],
        index=["Total Assets", "Stockholders Equity", "Total Debt"], columns=cols,
    )
    cyclical = StatementHistory(
        symbol="MU",
        prices=daily_prices(date(2021, 1, 1), 2200, start_price=100.0, daily_growth=0.0004),
        frames=AnnualFrames(income=income, balance=balance, cashflow=None, source="sec"),
        metadata={"sector": "Technology", "currency": "USD", "financial_currency": "USD"},
    )
    steady = us_filer("MSFT", margin=0.35)

    result = run_backtest([cyclical, steady], [date(2024, 12, 31), date(2025, 12, 31)], horizon_months=12)
    by_key = {(o.ticker, o.as_of.year): o for o in result.observations}

    # FY2025 is public by end-2025: 60% margin against a 20% median, debt up 233%.
    peak = by_key[("MU", 2025)]
    assert peak.peak_margin and peak.material_concerns == 2 and peak.material_strengths == 0
    # A year earlier only FY2022–24 were public: three years are too few to call a cycle.
    assert not by_key[("MU", 2024)].peak_margin
    assert not by_key[("MSFT", 2025)].peak_margin and by_key[("MSFT", 2025)].material_concerns == 0
