"""Scorecards — several companies on one comparable footing.

A vague question ("a chip maker with good fundamentals", "AMD or Nvidia?")
is answered by putting companies side by side. A `Scorecard` is one
company's line in that comparison: the two deterministic pillars, the
numbers behind them, what the newest data flags, and whether Claude has
judged the rest yet. `score_tickers` builds a ranked `Shortlist` of them;
`screen --score` and `compare` both end here.

Companies are ranked on the deterministic pillars only (`RankBy`). The
composite mixes in Claude's leadership / prospects / news judgments, which
exist for some names and sit at a neutral 50 for the rest — ranking on it
would rank "has been analysed" as much as "is good".

A row refreshed within the reuse window is taken from the cache instead of
being fetched again: statements change quarterly, so a follow-up question
minutes later should not wait on the network. The card says when it was
refreshed; `/analyse-stock` always fetches live.

Companies scored here are scored in passing: cached for the next question,
not added to what the user follows (`fundamental_engine.tracking`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from data_sources.fx_source import major_unit
from fundamental_engine.engine import Engine, RefreshResult, RefreshStatus
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.ticker import canonical
from fundamental_engine.universe import load_scores
from scorers.base import Gate, Pillar, Verdict
from scorers.composite_scorer import deterministic_composite
from scorers.early_signals import EarlySignal
from scorers.risk_profile import RiskProfile, risk_discount

DEFAULT_REUSE_HOURS = 12.0  # a cached row this fresh is not fetched again
MAX_CONCURRENT_FETCHES = 3
FETCH_START_SPACING_SECONDS = 1.0  # one new fetch a second: polite to screener.in and SEC

# The pillars Claude judges; a card names the ones still at the neutral prior.
_JUDGED_PILLARS: tuple[Pillar, ...] = (Pillar.LEADERSHIP, Pillar.PROSPECTS, Pillar.NEWS)


class RankBy(StrEnum):
    """What a shortlist is ordered by — always a deterministic number."""

    FUNDAMENTALS = "fundamentals"  # how good is the business
    VALUATION = "valuation"  # how reasonable is the price
    BOTH = "both"  # the two together, at their composite weights
    RISK_ADJUSTED = "risk-adjusted"  # both, less a discount for measured risk (the default)


@dataclass(frozen=True, slots=True)
class Scorecard:
    """One company in a comparison."""

    ticker: str
    company_name: str
    country: str
    industry: str
    fundamentals: float
    valuation: float | None  # None when no price ratio could be scored
    composite: float
    verdict: Verdict
    awaiting_judgment: tuple[Pillar, ...]  # Claude pillars still at the neutral prior
    integrity_veto: bool
    fundamentals_evidence: tuple[str, ...]
    valuation_evidence: tuple[str, ...]
    early_signals: tuple[EarlySignal, ...]
    refreshed_at: str  # ISO timestamp of the fetch behind these numbers
    reused: bool  # taken from the cache this run, not fetched
    market_cap_base: float | None = None  # in the base currency; None without an FX rate
    # How risky it is to hold, measured: price swings, liquidity, record (never assumed from size).
    risk: RiskProfile = field(default_factory=RiskProfile)
    traded_per_day_base: float | None = None  # an average day's trading, base currency
    # Set by a screen, which knows the field the company was found in:
    size_band: str = ""  # which third of the screen's companies, by market cap
    preview: float | None = None  # share of the rubric's marks cleared, 0–100
    # Market cap as a share of the largest company the screen matched: how far
    # the company stands from its field's leader in scale.
    scale_vs_largest_pct: float | None = None

    @classmethod
    def of(cls, extended: ExtendedScore, *, reused: bool) -> Scorecard:
        score = extended.score
        return cls(
            market_cap_base=in_base_currency(extended, extended.market_cap),
            risk=extended.risk,
            traded_per_day_base=in_base_currency(extended, extended.risk.avg_daily_traded_value),
            ticker=score.ticker,
            company_name=extended.company_name,
            country=extended.country,
            industry=extended.industry,
            fundamentals=score.fundamentals_score,
            valuation=None if Pillar.VALUATION in score.unscored else score.valuation_score,
            composite=score.composite,
            verdict=score.verdict,
            awaiting_judgment=tuple(p for p in _JUDGED_PILLARS if p in score.unscored),
            integrity_veto=Gate.INTEGRITY_VETO in score.gates_applied,
            fundamentals_evidence=tuple(score.evidence.get(Pillar.FUNDAMENTALS, ())),
            valuation_evidence=tuple(score.evidence.get(Pillar.VALUATION, ())),
            early_signals=tuple(extended.early_signals),
            refreshed_at=score.last_updated,
            reused=reused,
        )

    @property
    def both(self) -> float:
        """Fundamentals and valuation together — the deterministic composite."""
        return round(deterministic_composite(self.fundamentals, self.valuation), 1)

    @property
    def risk_discount(self) -> float:
        """Points off for measured risk when ranking (see `scorers.risk_profile`)."""
        return risk_discount(self.risk)

    @property
    def risk_adjusted(self) -> float:
        """Reward less risk: `both`, discounted for how risky the company is to hold."""
        return round(self.both - self.risk_discount, 1)

    @property
    def judged(self) -> bool:
        """Claude has scored leadership, prospects and news for this company."""
        return not self.awaiting_judgment

    def rank_value(self, by: RankBy) -> float:
        """Higher is better. An unscored valuation ranks below every scored one."""
        if by is RankBy.FUNDAMENTALS:
            return self.fundamentals
        if by is RankBy.VALUATION:
            return self.valuation if self.valuation is not None else -1.0
        if by is RankBy.RISK_ADJUSTED:
            return self.risk_adjusted
        return self.both

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "company": self.company_name,
            "country": self.country,
            "industry": self.industry,
            "fundamentals": self.fundamentals,
            "valuation": self.valuation,
            "both": self.both,
            "risk_discount": self.risk_discount,
            "risk_adjusted": self.risk_adjusted,
            "scale_vs_largest_pct": self.scale_vs_largest_pct,
            "composite": self.composite,
            "verdict": self.verdict.value,
            "market_cap_base": round(self.market_cap_base) if self.market_cap_base else None,
            "size_band": self.size_band,
            "preview": self.preview,
            "risk": {
                "volatility_1y_pct": self.risk.volatility_1y_pct,
                "worst_fall_3y_pct": self.risk.worst_fall_3y_pct,
                "traded_per_day_base": (
                    round(self.traded_per_day_base) if self.traded_per_day_base else None
                ),
                "daily_turnover_pct": self.risk.daily_turnover_pct,
                "price_history_years": self.risk.price_history_years,
                "fiscal_years": self.risk.fiscal_years,
                "loss_years": self.risk.loss_years,
            },
            "awaiting_judgment": [pillar.value for pillar in self.awaiting_judgment],
            "integrity_veto": self.integrity_veto,
            "why": {
                "fundamentals": list(self.fundamentals_evidence),
                "valuation": list(self.valuation_evidence),
            },
            "early_signals": [signal.to_dict() for signal in self.early_signals],
            "refreshed_at": self.refreshed_at,
            "reused": self.reused,
        }


@dataclass(slots=True)
class Shortlist:
    """Scored companies, best first, and the tickers that could not be scored."""

    ranked_by: RankBy
    cards: list[Scorecard] = field(default_factory=list)
    not_scored: list[RefreshResult] = field(default_factory=list)

    @property
    def reused(self) -> int:
        return sum(1 for card in self.cards if card.reused)

    def to_dict(self) -> dict[str, object]:
        return {
            "ranked_by": self.ranked_by.value,
            "scored": [card.to_dict() for card in self.cards],
            "not_scored": [result.to_dict() for result in self.not_scored],
        }


async def score_tickers(
    tickers: Sequence[str],
    engine: Engine,
    cache_dir: Path,
    *,
    rank_by: RankBy = RankBy.RISK_ADJUSTED,
    reuse_hours: float = DEFAULT_REUSE_HOURS,
) -> Shortlist:
    """Score every ticker — from the cache when fresh, else live — and rank them."""
    wanted = list(dict.fromkeys(canonical(ticker) for ticker in tickers))
    cached = load_scores(cache_dir)
    stale = [ticker for ticker in wanted if not _fresh(cached.get(ticker), reuse_hours)]
    results = {result.ticker: result for result in await _refresh(stale, engine)}
    scores = load_scores(cache_dir) if stale else cached

    shortlist = Shortlist(ranked_by=rank_by)
    for ticker in wanted:
        result = results.get(ticker)
        extended = scores.get(ticker)
        if extended is None or (result is not None and result.status != RefreshStatus.OK):
            shortlist.not_scored.append(
                result or RefreshResult(ticker=ticker, status=RefreshStatus.NO_DATA)
            )
            continue
        shortlist.cards.append(Scorecard.of(extended, reused=result is None))
    shortlist.cards = _with_scale(shortlist.cards)
    shortlist.cards.sort(key=lambda card: card.rank_value(rank_by), reverse=True)
    return shortlist


def _with_scale(cards: list[Scorecard]) -> list[Scorecard]:
    """Each card's market cap as a share of the largest company scored beside it.

    A screen replaces this with the share of the largest company it matched.
    """
    largest = max((card.market_cap_base or 0.0 for card in cards), default=0.0)
    if largest <= 0:
        return cards
    return [
        replace(card, scale_vs_largest_pct=round(card.market_cap_base / largest * 100, 3))
        if card.market_cap_base
        else card
        for card in cards
    ]


def in_base_currency(extended: ExtendedScore, amount: float | None) -> float | None:
    """A trading-currency amount in the base currency, at the FX rate cached with the row."""
    if amount is None or extended.fx_rate_to_base is None:
        return None
    # The amount is in the currency's major unit (pounds); the rate is per quoted unit (pence).
    minor_units_per_major = major_unit(extended.currency)[1]
    return amount * minor_units_per_major * extended.fx_rate_to_base


def _fresh(extended: ExtendedScore | None, reuse_hours: float) -> bool:
    """Was this row fetched within the reuse window? An unreadable timestamp is not fresh."""
    if extended is None or reuse_hours <= 0:
        return False
    try:
        age = datetime.now() - datetime.fromisoformat(extended.score.last_updated)
    except (ValueError, TypeError):  # not a date, or one carrying a time zone
        return False
    return age <= timedelta(hours=reuse_hours)


async def _refresh(tickers: Sequence[str], engine: Engine) -> list[RefreshResult]:
    """Live fetch + score for each ticker: a few at once, one new start per second."""
    gate = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def refresh(position: int, ticker: str) -> RefreshResult:
        await asyncio.sleep(position * FETCH_START_SPACING_SECONDS)
        async with gate:
            return await engine.refresh_one(ticker, follow=False)

    return list(await asyncio.gather(*(refresh(i, ticker) for i, ticker in enumerate(tickers))))
