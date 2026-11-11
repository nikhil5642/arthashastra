"""Every suite on synthetic histories built to make its claim true, then false.

Each history is ten year-ends × twelve tickers (ranking claims need 10+
names on a date) with deterministic noise, so a claim that holds PASSes,
its mirror image FAILs, and nothing NO DATAs.
"""

from __future__ import annotations

import pkgutil
from collections.abc import Callable
from datetime import date

import pytest

import backtests.suites
from backtests.engine.metrics import Observation, attach_excess_returns
from backtests.suite import Outcome, Suite
from backtests.suites import (
    ALL,
    bank_rubric,
    combined_score,
    dip_signals,
    early_concerns,
    early_strengths,
    fundamentals_score,
    growth_veto,
    peak_margin_guard,
    valuation_score,
    value_trap_guard,
    verdict_bands,
)

DATES, TICKERS = 10, 12


def noise(i: int, j: int) -> float:
    """Deterministic wiggle in [-2, 2] so per-date statistics vary."""
    return float((i * 7 + j * 3) % 5 - 2)


def history(build: Callable[[int, int], dict[str, object]]) -> list[Observation]:
    observations: list[Observation] = []
    for i in range(DATES):
        for j in range(TICKERS):
            fields: dict[str, object] = {
                "ticker": f"T{j}", "as_of": date(2010 + i, 12, 31), "is_bank": False,
                "fundamentals": 50.0, "completeness": 100.0, "valuation": None,
                "forward_return_pct": noise(i, j),
            }
            fields.update(build(i, j))
            observations.append(Observation(**fields))  # type: ignore[arg-type]
    attach_excess_returns(observations)
    return observations


def ranked_by(field: str, sign: int, **extra: object) -> list[Observation]:
    return history(
        lambda i, j: {field: 30.0 + 5 * j, "forward_return_pct": sign * 3.0 * j + noise(i, j), **extra}
    )


def flagged(flag: str, sign: int, **extra: object) -> list[Observation]:
    """Half the tickers carry `flag`; they return 4% more (sign=+1) or less (−1)."""
    return history(
        lambda i, j: {
            flag: j % 2 == 0, "forward_return_pct": sign * 4.0 * (j % 2 == 0) + noise(i, j), **extra,
        }
    )


def combined_history(sign: int) -> list[Observation]:
    """Fundamentals barely differ; returns follow (or oppose) a wide valuation spread."""
    return history(
        lambda i, j: {
            "fundamentals": 50.0 + (j % 2), "valuation": 10.0 + 7 * j,
            "forward_return_pct": sign * 3.0 * j + noise(i, j),
        }
    )


def bands_history(sign: int) -> list[Observation]:
    """Half STRONG_BUY, half RED_FLAG on the deterministic composite."""
    return history(
        lambda i, j: {
            "fundamentals": 90.0 if j < 6 else 20.0, "valuation": 90.0 if j < 6 else 20.0,
            "forward_return_pct": sign * (5.0 if j < 6 else -5.0) + noise(i, j),
        }
    )


def value_trap_history(sign: int) -> list[Observation]:
    """The cheapest third (four tickers) holds one slow grower per date."""
    return history(
        lambda i, j: {
            "earnings_yield": 12.0 - j, "slow_growth": j == (i % 2),
            "forward_return_pct": sign * (-4.0 if j == (i % 2) else 0.0) + noise(i, j) / 2,
        }
    )


def peak_margin_history(sign: int) -> list[Observation]:
    """The cheapest third (four tickers) holds one company on peak margins per date."""
    return history(
        lambda i, j: {
            "earnings_yield": 12.0 - j, "peak_margin": j == (i % 2),
            "forward_return_pct": sign * (-4.0 if j == (i % 2) else 0.0) + noise(i, j) / 2,
        }
    )


def signalled(field: str, sign: int) -> list[Observation]:
    """Half the tickers carry one material early signal; they return 4% more (+1) or less (−1)."""
    return history(
        lambda i, j: {
            field: 1 if j % 2 == 0 else 0, "forward_return_pct": sign * 4.0 * (j % 2 == 0) + noise(i, j),
        }
    )


CASES: list[tuple[Suite, Callable[[int], list[Observation]]]] = [
    (fundamentals_score.SUITE, lambda sign: ranked_by("fundamentals", sign)),
    (valuation_score.SUITE, lambda sign: ranked_by("valuation", sign)),
    (combined_score.SUITE, combined_history),
    (verdict_bands.SUITE, bands_history),
    (growth_veto.SUITE, lambda sign: flagged("slow_growth", -sign)),
    (value_trap_guard.SUITE, value_trap_history),
    (peak_margin_guard.SUITE, peak_margin_history),
    (early_concerns.SUITE, lambda sign: signalled("material_concerns", -sign)),
    (early_strengths.SUITE, lambda sign: signalled("material_strengths", sign)),
    (dip_signals.SUITE, lambda sign: flagged("dip_signal", sign)),
    (bank_rubric.SUITE, lambda sign: ranked_by("fundamentals", sign, is_bank=True)),
]


@pytest.mark.parametrize(("suite", "build"), CASES, ids=[suite.name for suite, _ in CASES])
def test_suite_passes_when_its_claim_holds_and_fails_when_reversed(suite, build):
    holds = suite.evaluate(build(+1))
    assert holds.outcome is Outcome.PASS, holds.finding
    assert holds.table is not None and holds.table.rows

    reversed_ = suite.evaluate(build(-1))
    assert reversed_.outcome is Outcome.FAIL, reversed_.finding


@pytest.mark.parametrize("suite", ALL, ids=[suite.name for suite in ALL])
def test_every_suite_says_no_data_on_an_empty_history(suite):
    assert suite.evaluate([]).outcome is Outcome.NO_DATA


def test_every_suite_module_is_registered_under_its_own_name():
    modules = {m.name for m in pkgutil.iter_modules(backtests.suites.__path__)}
    assert modules == {suite.name for suite in ALL}
    assert len(ALL) == len(modules)


def test_growth_veto_ignores_banks():
    banks_only = flagged("slow_growth", -1, is_bank=True)
    assert growth_veto.SUITE.evaluate(banks_only).outcome is Outcome.NO_DATA


def test_value_trap_universe_is_the_cheapest_profitable_third():
    observations = value_trap_history(+1)
    cheap = value_trap_guard.cheapest_third(observations)
    assert len(cheap) == DATES * TICKERS // 3
    assert all(o.earnings_yield is not None and o.earnings_yield >= 9.0 for o in cheap)
    loss_makers = history(lambda i, j: {"earnings_yield": None if j else -3.0})
    assert value_trap_guard.cheapest_third(loss_makers) == []


def test_valuation_suite_skips_unscored_observations():
    unscored = history(lambda i, j: {"forward_return_pct": 3.0 * j + noise(i, j)})
    assert valuation_score.SUITE.evaluate(unscored).outcome is Outcome.NO_DATA
