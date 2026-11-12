"""Risk profile — what can be measured about how risky a company is to hold.

Smaller companies are riskier on average, and measurably so. Across 293
cached companies in November 2026 the yearly swing of the share price ran
34% for those above $200B, 46% for $10–200B, 56% for $2–10B, 64% for
$300M–2B and 70% below that; years ending in a loss rose from 6% of the
record to 32%. But a fifth of the companies above $10B were wilder than
the median one below $2B, and the calmest small companies were as steady
as any giant. Size is a proxy. This module measures what it stands for:

    price risk      how much the price swings in a year; its deepest fall
                    in three
    liquidity       what trades on an average day, and what share of the
                    company that is
    record          how many fiscal years stand behind the scores, and how
                    many of them ended in a loss

Leverage, coverage and cash conversion are risk too, and the fundamentals
pillar already scores them for every company alike.

These are facts, and they move no pillar. A search stays blind to size
(`fundamental_engine.preview`) so that every company gets looked at; the
profile is how the answer stays honest about what holding one involves.

Risk against reward: `risk_discount` turns the profile into points taken
off when companies are ranked against each other (`RankBy.RISK_ADJUSTED`),
so that a 95 that swings 77% a year and lost money in a third of its
record does not outrank a 92 that does neither. It is a ranking aid and
never changes the composite or a verdict. What it cannot see — whether a
small company depends on one supplier, or can be out-spent by the leader
of its industry — is judged by Claude in the prospects pillar.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from data_sources.fundamentals_source import BalanceSheetSnapshot


@dataclass(frozen=True, slots=True)
class RiskProfile:
    """Measured facts; None wherever the data to measure one is missing."""

    volatility_1y_pct: float | None = None  # annualised swing of daily returns
    worst_fall_3y_pct: float | None = None  # deepest high-to-low fall in three years
    avg_daily_traded_value: float | None = None  # trading currency, major unit
    daily_turnover_pct: float | None = None  # of the market cap, traded on an average day
    price_history_years: float | None = None  # the record the price measures rest on
    fiscal_years: int = 0  # statements behind the scores
    loss_years: int = 0  # of those, years that ended in a loss

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> RiskProfile:
        """Parse a cached profile; anything that doesn't fit is simply unknown."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            volatility_1y_pct=_number(data.get("volatility_1y_pct")),
            worst_fall_3y_pct=_number(data.get("worst_fall_3y_pct")),
            avg_daily_traded_value=_number(data.get("avg_daily_traded_value")),
            daily_turnover_pct=_number(data.get("daily_turnover_pct")),
            price_history_years=_number(data.get("price_history_years")),
            fiscal_years=int(_number(data.get("fiscal_years")) or 0),
            loss_years=int(_number(data.get("loss_years")) or 0),
        )


def risk_profile(snap: BalanceSheetSnapshot) -> RiskProfile:
    """Pure: the profile a snapshot supports."""
    traded = snap.avg_daily_traded_value
    margins = snap.net_margin_annual_trend
    return RiskProfile(
        volatility_1y_pct=snap.volatility_1y_pct,
        worst_fall_3y_pct=snap.worst_fall_3y_pct,
        avg_daily_traded_value=round(traded) if traded is not None else None,
        daily_turnover_pct=(
            round(traded / snap.market_cap * 100, 3)
            if traded is not None and snap.market_cap
            else None
        ),
        price_history_years=snap.price_history_years,
        fiscal_years=len(margins),
        loss_years=sum(1 for margin in margins if margin < 0),
    )


# Risk discount, in points off a 0–100 score when ranking. Volatility: none
# at or below a calm 30%, half a point for each point above it, capped where
# a 70% swing is reached. Record: up to ten points for the share of loss
# years, five more when fewer than four fiscal years are on file.
CALM_VOLATILITY_PCT = 30.0
VOLATILITY_POINTS_PER_PCT = 0.5
MAX_VOLATILITY_DISCOUNT = 20.0
UNKNOWN_VOLATILITY_DISCOUNT = 10.0  # unknown is not calm
MAX_LOSS_YEAR_DISCOUNT = 10.0
MIN_FISCAL_YEARS = 4
SHORT_RECORD_DISCOUNT = 5.0
MAX_RISK_DISCOUNT = 30.0
# The swing an unmeasured price is assumed to have when sizing a position:
# the volatility that costs the same ten points as "unknown" does above.
ASSUMED_VOLATILITY_PCT = CALM_VOLATILITY_PCT + UNKNOWN_VOLATILITY_DISCOUNT / VOLATILITY_POINTS_PER_PCT


def position_scale(volatility_1y_pct: float | None) -> float:
    """Share of a full position to hold, 0–1: a price that swings twice as much gets half.

    A calm price (30% a year or less) gets the full slice, so every position
    carries about the same swing in money. Unknown is sized as 50%, not as calm.
    """
    volatility = ASSUMED_VOLATILITY_PCT if volatility_1y_pct is None else volatility_1y_pct
    return round(CALM_VOLATILITY_PCT / max(volatility, CALM_VOLATILITY_PCT), 3)


def risk_discount(profile: RiskProfile) -> float:
    """Points to take off a score when ranking this company against others, 0–30."""
    if profile.volatility_1y_pct is None:
        volatility = UNKNOWN_VOLATILITY_DISCOUNT
    else:
        excess = max(0.0, profile.volatility_1y_pct - CALM_VOLATILITY_PCT)
        volatility = min(MAX_VOLATILITY_DISCOUNT, excess * VOLATILITY_POINTS_PER_PCT)
    record = SHORT_RECORD_DISCOUNT if profile.fiscal_years < MIN_FISCAL_YEARS else 0.0
    if profile.fiscal_years:
        record += MAX_LOSS_YEAR_DISCOUNT * profile.loss_years / profile.fiscal_years
    return round(min(MAX_RISK_DISCOUNT, volatility + record), 1)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
