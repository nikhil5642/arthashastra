"""Point-in-time backtest runner — score every ticker on every date.

For each listing and rebalance date it rebuilds the snapshot from data
that was public on that date (`snapshot_as_of`, which also prices it at
that day's close), scores fundamentals and valuation with the live
scorers, checks the quality-dip gate on the closes known that evening,
and records the total return over the next period.

The dip gate's quality bar is the deterministic composite (fundamentals +
valuation at their live relative weights): Claude's qualitative pillars
have no history to replay.

Each observation also records the early signals the past snapshot shows.
What can fire there is what the history can replay: the signals built on
fiscal-year statements for every listing (a margin above its own median,
debt or goodwill jumping year on year, negative equity, the share count
where the source reports it), plus the quarterly and shareholding signals
for Indian listings over the three or so years screener publishes. News,
price-drawdown and trailing-cash-flow signals never fire in a backtest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from backtests.engine.metrics import Observation, attach_excess_returns
from backtests.engine.returns import forward_return
from data_sources.fundamentals_source import BalanceSheetSnapshot, is_bank_snapshot
from data_sources.fundamentals_source.history import (
    DEFAULT_REPORTING_LAG_DAYS,
    StatementHistory,
    snapshot_as_of,
)
from data_sources.price_series import PriceSeries
from scorers.base import StockScore, verdict_for
from scorers.early_signals import Tone, detect_early_signals, of_tone
from scorers.fundamentals_scorer import score_fundamentals
from scorers.fundamentals_scorer.industrial import growth_deceleration_cap
from scorers.valuation_scorer import repeatable_pe, score_valuation, sustainable_earnings
from screener.dip_detector import check_dip
from screener.price_history import snapshot_from_closes
from signals.signal_bus import Severity

# The qualitative pillars have no history; the dip gate never reads them.
UNKNOWN_PILLAR_SCORE = 50.0


@dataclass(slots=True)
class Coverage:
    """How deep one ticker's backtest could go — reported, never hidden."""

    ticker: str
    fundamentals_source: str
    price_history_start: date | None
    scored_dates: int
    first_scored: date | None
    last_scored: date | None


@dataclass(slots=True)
class BacktestResult:
    observations: list[Observation]
    coverage: list[Coverage]


def run_backtest(
    histories: Sequence[StatementHistory],
    dates: Sequence[date],
    *,
    horizon_months: int,
    reporting_lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
) -> BacktestResult:
    observations: list[Observation] = []
    coverage: list[Coverage] = []
    for history in histories:
        prices = PriceSeries.from_rows(history.prices)
        total_return = PriceSeries.from_rows(history.total_return) if history.total_return else prices
        scored: list[Observation] = []
        for as_of in dates:
            if prices.close_on_or_before(as_of) is None:
                continue  # not listed yet (or halted): nothing to buy on this date
            snap = snapshot_as_of(history, as_of, reporting_lag_days=reporting_lag_days, prices=prices)
            if snap is None:
                continue
            observation = _observe(snap, as_of, forward_return(total_return, as_of, horizon_months))
            observation.dip_signal = _dip_fired(snap, observation, prices, as_of)
            scored.append(observation)
        observations.extend(scored)
        coverage.append(
            Coverage(
                ticker=history.symbol,
                fundamentals_source=history.fundamentals_source,
                price_history_start=prices.first_date,
                scored_dates=len(scored),
                first_scored=scored[0].as_of if scored else None,
                last_scored=scored[-1].as_of if scored else None,
            )
        )
    attach_excess_returns(observations)
    return BacktestResult(observations=observations, coverage=coverage)


def _observe(snap: BalanceSheetSnapshot, as_of: date, forward_return_pct: float | None) -> Observation:
    fundamentals = score_fundamentals(snap)
    valuation = score_valuation(snap)
    # The statement-age signal is about today's cache, not about a past date.
    material = [
        signal
        for signal in detect_early_signals(snap, today=as_of)
        if signal.severity is not Severity.INFO
    ]
    return Observation(
        ticker=snap.ticker,
        as_of=as_of,
        is_bank=is_bank_snapshot(snap),
        fundamentals=fundamentals.score,
        completeness=fundamentals.data_completeness_pct,
        valuation=valuation.score if valuation.data_completeness_pct > 0 else None,
        forward_return_pct=forward_return_pct,
        slow_growth=growth_deceleration_cap(snap) is not None,
        earnings_yield=snap.earnings_yield,
        peak_margin=sustainable_earnings(snap).above_cycle,
        repeatable_pe=repeatable_pe(snap),
        material_concerns=len(of_tone(material, Tone.CONCERN)),
        material_strengths=len(of_tone(material, Tone.STRENGTH)),
    )


def _dip_fired(
    snap: BalanceSheetSnapshot, observation: Observation, prices: PriceSeries, as_of: date
) -> bool:
    price_snapshot = snapshot_from_closes(snap.ticker, prices.closes_through(as_of))
    if price_snapshot is None:
        return False
    check = check_dip(
        score=_deterministic_score(observation),
        prices=price_snapshot,
        yoy_pat_growth=snap.latest_yoy_pat_growth,
        yoy_revenue_growth=snap.latest_yoy_revenue_growth,
    )
    return check.qualifies


def _deterministic_score(observation: Observation) -> StockScore:
    """A StockScore whose composite is the deterministic composite, for the dip gate."""
    composite = round(observation.combined, 1)
    valuation = observation.valuation if observation.valuation is not None else UNKNOWN_PILLAR_SCORE
    return StockScore(
        ticker=observation.ticker,
        leadership_score=UNKNOWN_PILLAR_SCORE,
        fundamentals_score=observation.fundamentals,
        prospects_score=UNKNOWN_PILLAR_SCORE,
        news_score=UNKNOWN_PILLAR_SCORE,
        composite=composite,
        verdict=verdict_for(composite),
        confidence=round(observation.completeness / 100, 2),
        data_completeness_pct=observation.completeness,
        valuation_score=valuation,
    )
