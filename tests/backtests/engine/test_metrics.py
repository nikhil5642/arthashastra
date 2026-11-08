"""Backtest statistics tests — known answers on small inputs."""

from __future__ import annotations

import math
from datetime import date

import pytest

from backtests.engine.metrics import (
    MIN_CROSS_SECTION,
    Observation,
    ReturnStats,
    ScoreKind,
    attach_excess_returns,
    band_returns,
    compare_groups,
    cross_sectional_ics,
    ic_difference,
    largest_cross_section,
    mean_test,
    spearman,
    tercile_returns,
    tercile_spread,
)
from scorers.base import Verdict

D1, D2 = date(2024, 12, 31), date(2025, 12, 31)


def obs(
    ticker: str,
    day: date,
    fundamentals: float,
    ret: float | None,
    *,
    valuation: float | None = None,
    excess: float | None = None,
) -> Observation:
    return Observation(
        ticker=ticker, as_of=day, is_bank=False, fundamentals=fundamentals, completeness=100.0,
        valuation=valuation, forward_return_pct=ret, excess_return_pct=excess,
    )


def test_spearman_known_values():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833)  # ties averaged
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # no variance
    assert spearman([1], [1]) is None


def test_combined_score_uses_the_live_relative_weights():
    # 35% fundamentals : 15% valuation → 0.7 / 0.3 once rescaled.
    assert obs("MSFT", D1, 80, 1.0, valuation=40).combined == pytest.approx(68.0)
    # Unscored valuation counts as neutral 50, exactly as in the live composite.
    assert obs("BABA", D1, 80, 1.0).combined == pytest.approx(71.0)
    assert obs("BABA", D1, 80, 1.0).score(ScoreKind.VALUATION) is None
    assert obs("MSFT", D1, 80, 1.0, valuation=40).score(ScoreKind.FUNDAMENTALS) == 80


def cross_section(day: date, names: int, *, ret_sign: int = 1, **fields: float) -> list[Observation]:
    """`names` tickers whose fundamentals and returns both rise with the index."""
    return [
        obs(f"T{j}", day, 40.0 + 5 * j, ret_sign * 2.0 * j, valuation=fields.get("valuation", 90.0 - 5 * j))
        for j in range(names)
    ]


def test_cross_sectional_ic_needs_ten_scored_tickers_with_returns():
    observations = [
        *cross_section(D1, MIN_CROSS_SECTION),
        *cross_section(D2, MIN_CROSS_SECTION - 1),  # one name short → skipped
        obs("005930.KS", D2, 70, None),  # no forward window → ignored
    ]
    [ic] = cross_sectional_ics(observations, ScoreKind.FUNDAMENTALS)
    assert (ic.as_of, ic.tickers) == (D1, 10) and ic.ic == pytest.approx(1.0)
    [valuation_ic] = cross_sectional_ics(observations, ScoreKind.VALUATION)
    assert valuation_ic.ic == pytest.approx(-1.0)
    assert largest_cross_section(observations, ScoreKind.FUNDAMENTALS) == 10
    assert largest_cross_section([], ScoreKind.FUNDAMENTALS) == 0


def test_mean_test_t_stat_and_edge_cases():
    test = mean_test([0.2, 0.4])
    assert (test.n, test.positive_share) == (2, 1.0)
    assert test.mean == pytest.approx(0.3) and test.t_stat == pytest.approx(3.0)
    assert mean_test([]).mean is None
    assert mean_test([0.5]).t_stat is None  # one value has no spread
    assert mean_test([0.1, 0.1]).t_stat == math.inf  # every value agreed exactly
    assert mean_test([0.0, 0.0]).t_stat is None


def test_ic_difference_pairs_dates():
    # Fundamentals barely fall across names; a wide valuation spread (and the
    # returns) rise — so the blend ranks perfectly and fundamentals inversely.
    observations = [
        obs(f"T{j}", day, 60.0 - j, 2.0 * j, valuation=10.0 * j)
        for day in (D1, D2)
        for j in range(MIN_CROSS_SECTION)
    ]
    gain = ic_difference(observations, ScoreKind.COMBINED, ScoreKind.FUNDAMENTALS)
    assert gain.n == 2 and gain.mean == pytest.approx(2.0)  # IC +1 vs −1 on both dates


def test_terciles_split_each_date_into_thirds():
    observations = cross_section(D1, 12)  # returns 0, 2, …, 22 → thirds of four
    spread = tercile_spread(observations, ScoreKind.FUNDAMENTALS)
    assert spread.n == 1 and spread.mean == pytest.approx(19.0 - 3.0)
    assert tercile_spread(observations[:9], ScoreKind.FUNDAMENTALS).n == 0
    bottom, middle, top = tercile_returns(observations, ScoreKind.FUNDAMENTALS)
    assert (bottom.mean_return_pct, middle.mean_return_pct, top.mean_return_pct) == (3.0, 11.0, 19.0)


def test_band_returns_group_by_verdict():
    rows = {row.verdict: row.stats for row in band_returns([
        obs("MSFT", D1, 85, 10.0), obs("NVDA", D1, 82, -2.0), obs("RBRK", D1, 30, 50.0),
    ], ScoreKind.FUNDAMENTALS)}
    assert rows[Verdict.STRONG_BUY] == ReturnStats(
        count=2, mean_return_pct=4.0, mean_excess_pct=None, hit_rate=0.5
    )
    assert rows[Verdict.RED_FLAG].count == 1
    assert rows[Verdict.HOLD] == ReturnStats(
        count=0, mean_return_pct=None, mean_excess_pct=None, hit_rate=None
    )


def test_excess_returns_remove_each_dates_average():
    observations = [
        obs("MSFT", D1, 90, 30.0), obs("GOOGL", D1, 80, 20.0), obs("RBRK", D1, 40, -20.0),
        obs("SAP.DE", D2, 70, 5.0), obs("7203.T", D2, 70, None),
    ]
    attach_excess_returns(observations)
    assert [o.excess_return_pct for o in observations] == [20.0, 10.0, -30.0, 0.0, None]


def test_compare_groups_uses_welch_t_on_excess_returns():
    group = [obs("A", D1, 50, 10.0, excess=2.0), obs("B", D1, 50, 12.0, excess=4.0)]
    rest = [obs("C", D1, 50, 5.0, excess=-1.0), obs("D", D1, 50, 3.0, excess=-3.0)]
    comparison = compare_groups(group, rest)
    assert comparison.difference_pct == pytest.approx(5.0)
    assert comparison.t_stat == pytest.approx(5.0 / math.sqrt(2.0))  # each variance is 2
    assert comparison.a.mean_return_pct == 11.0 and comparison.b.hit_rate == 1.0
    assert compare_groups([], rest).difference_pct is None
    assert compare_groups(group[:1], rest[:1]).t_stat is None  # one each: no spread
