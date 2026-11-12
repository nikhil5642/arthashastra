"""Scorecard tests — what a card says, how a shortlist is ranked, and when the cache is reused."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fundamental_engine import scorecard
from fundamental_engine.cache import JsonStore
from fundamental_engine.engine import Engine, RefreshResult, RefreshStatus
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.scorecard import RankBy, Scorecard, score_tickers
from scorers.base import Gate, Pillar, StockScore, Verdict
from scorers.early_signals import EarlySignal, EarlySignalKind, Tone
from signals.signal_bus import Severity


def _extended(ticker: str, fundamentals: float, valuation: float, *,
              unscored: tuple[Pillar, ...] = (), updated: datetime | None = None,
              gates: tuple[Gate, ...] = ()) -> ExtendedScore:
    score = StockScore(
        ticker=ticker, leadership_score=50, fundamentals_score=fundamentals,
        valuation_score=valuation, prospects_score=50, news_score=50, composite=64.0,
        verdict=Verdict.HOLD, confidence=0.4, data_completeness_pct=60,
        evidence={Pillar.FUNDAMENTALS: ["ROE = 31.7% → 100/100"],
                  Pillar.VALUATION: ["Earnings yield = 4.07% (P/E 24.6) → 66/100"]},
        last_updated=(updated or datetime.now()).isoformat(timespec="seconds"),
        gates_applied=list(gates), unscored=list(unscored),
    )
    concern = EarlySignal(EarlySignalKind.DEBT_RISING, Tone.CONCERN, Severity.ALERT, "Debt rose 60%.")
    return ExtendedScore(score=score, company_name=f"{ticker} Inc.", country="Taiwan",
                         industry="Semiconductors", early_signals=[concern])


def test_a_card_carries_the_scores_the_evidence_and_what_is_still_unjudged():
    pending = (Pillar.LEADERSHIP, Pillar.PROSPECTS, Pillar.NEWS)
    card = Scorecard.of(_extended("2330.TW", 92.3, 54.8, unscored=pending), reused=False)
    assert (card.fundamentals, card.valuation) == (92.3, 54.8)
    assert card.both == pytest.approx((92.3 * 35 + 54.8 * 15) / 50, abs=0.05)
    assert card.awaiting_judgment == pending and not card.judged

    row = card.to_dict()
    assert row["why"] == {
        "fundamentals": ["ROE = 31.7% → 100/100"],
        "valuation": ["Earnings yield = 4.07% (P/E 24.6) → 66/100"],
    }
    assert row["awaiting_judgment"] == ["leadership", "prospects", "news"]
    assert row["early_signals"][0]["message"] == "Debt rose 60%."
    assert (row["company"], row["industry"], row["verdict"], row["reused"]) == (
        "2330.TW Inc.", "Semiconductors", "HOLD", False,
    )

    judged = Scorecard.of(_extended("NVDA", 91.9, 75.3, gates=(Gate.INTEGRITY_VETO,)), reused=True)
    assert judged.judged and judged.integrity_veto and judged.to_dict()["integrity_veto"] is True


def test_an_unscored_valuation_is_shown_as_missing_and_ranks_last_on_price():
    receipt = Scorecard.of(_extended("RIO.BA", 80.0, 50.0, unscored=(Pillar.VALUATION,)), reused=False)
    pricey = Scorecard.of(_extended("ASML.AS", 88.0, 12.0), reused=False)
    assert receipt.valuation is None and receipt.to_dict()["valuation"] is None
    assert receipt.rank_value(RankBy.VALUATION) < pricey.rank_value(RankBy.VALUATION)
    # Together with fundamentals it counts as the neutral 50 the composite uses.
    assert receipt.both == pytest.approx((80.0 * 35 + 50.0 * 15) / 50)


class _FakeEngine(Engine):
    """Writes a score row for the tickers it 'knows'; anything else has no data."""

    def __init__(self, cache: Path, known: dict[str, tuple[float, float]]) -> None:
        self._cache = cache
        self._known = known
        self.fetched: list[str] = []
        self.followed: list[bool] = []

    async def refresh_one(self, ticker: str, *, follow: bool = True) -> RefreshResult:
        self.fetched.append(ticker)
        self.followed.append(follow)
        if ticker not in self._known:
            return RefreshResult(ticker=ticker, status=RefreshStatus.NO_DATA, detail="No fundamentals")
        fundamentals, valuation = self._known[ticker]
        _store(self._cache, _extended(ticker, fundamentals, valuation))
        return RefreshResult(ticker=ticker, status=RefreshStatus.OK)


def _store(cache: Path, extended: ExtendedScore) -> None:
    JsonStore(cache / "scores.json").upsert(extended.score.ticker, extended.to_dict())


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch) -> None:
    monkeypatch.setattr(scorecard, "FETCH_START_SPACING_SECONDS", 0)


async def test_a_shortlist_is_ranked_by_what_was_asked(tmp_path: Path):
    engine = _FakeEngine(tmp_path, {"NVDA": (91.9, 75.3), "2330.TW": (92.3, 54.8), "AVGO": (70.0, 30.0)})
    tickers = ["nvda", "2330.TW", "AVGO"]

    by_business = await score_tickers(tickers, engine, tmp_path, rank_by=RankBy.FUNDAMENTALS)
    assert [c.ticker for c in by_business.cards] == ["2330.TW", "NVDA", "AVGO"]
    by_price = await score_tickers(tickers, engine, tmp_path, rank_by=RankBy.VALUATION)
    assert [c.ticker for c in by_price.cards] == ["NVDA", "2330.TW", "AVGO"]
    both = await score_tickers(tickers, engine, tmp_path, rank_by=RankBy.BOTH)
    assert [c.ticker for c in both.cards] == ["NVDA", "2330.TW", "AVGO"]
    assert both.ranked_by is RankBy.BOTH and both.to_dict()["ranked_by"] == "both"
    default = await score_tickers(tickers, engine, tmp_path)
    assert default.ranked_by is RankBy.RISK_ADJUSTED  # reward less risk, unless asked otherwise


def test_the_default_ranking_sets_reward_against_risk():
    from scorers.risk_profile import RiskProfile

    wild = _extended("WILD", 95.0, 60.0)  # the better scores, a rough ride
    wild.risk = RiskProfile(volatility_1y_pct=77.0, fiscal_years=4, loss_years=1)
    steady = _extended("STEADY", 92.0, 60.0)
    steady.risk = RiskProfile(volatility_1y_pct=32.0, fiscal_years=4, loss_years=0)
    wild_card, steady_card = Scorecard.of(wild, reused=False), Scorecard.of(steady, reused=False)

    assert wild_card.both > steady_card.both  # on reward alone the wild one leads
    assert (wild_card.risk_discount, steady_card.risk_discount) == (22.5, 1.0)
    assert steady_card.rank_value(RankBy.RISK_ADJUSTED) > wild_card.rank_value(RankBy.RISK_ADJUSTED)
    assert wild_card.to_dict()["risk_adjusted"] == round(wild_card.both - 22.5, 1)
    assert (wild_card.fundamentals, wild_card.composite) == (95.0, 64.0)  # no score is changed


async def test_a_fresh_row_is_reused_and_a_stale_one_is_fetched_again(tmp_path: Path):
    _store(tmp_path, _extended("NVDA", 91.9, 75.3, updated=datetime.now() - timedelta(hours=2)))
    _store(tmp_path, _extended("AMD", 60.0, 40.0, updated=datetime.now() - timedelta(hours=30)))
    engine = _FakeEngine(tmp_path, {"AMD": (62.0, 41.0), "MU": (80.0, 68.0)})

    shortlist = await score_tickers(["NVDA", "AMD", "MU", "NVDA"], engine, tmp_path, reuse_hours=12)
    assert engine.fetched == ["AMD", "MU"]  # NVDA is two hours old; asked twice, listed once
    assert engine.followed == [False, False]  # scored in passing: not added to what the user follows
    assert {c.ticker: c.reused for c in shortlist.cards} == {"NVDA": True, "MU": False, "AMD": False}
    assert shortlist.reused == 1
    assert next(c for c in shortlist.cards if c.ticker == "AMD").fundamentals == 62.0  # the fresh fetch

    live = _FakeEngine(tmp_path, {"NVDA": (91.9, 75.3)})
    await score_tickers(["NVDA"], live, tmp_path, reuse_hours=0)
    assert live.fetched == ["NVDA"]  # zero hours: always live


async def test_what_cannot_be_scored_is_listed_with_the_reason(tmp_path: Path):
    # A week-old row exists for GONE, but today's fetch found nothing: the old numbers are not passed off as new.
    _store(tmp_path, _extended("GONE", 70.0, 70.0, updated=datetime.now() - timedelta(days=7)))
    engine = _FakeEngine(tmp_path, {"NVDA": (91.9, 75.3)})
    shortlist = await score_tickers(["NVDA", "GONE", "TYPO"], engine, tmp_path)
    assert [c.ticker for c in shortlist.cards] == ["NVDA"]
    assert [(r.ticker, r.status, r.detail) for r in shortlist.not_scored] == [
        ("GONE", RefreshStatus.NO_DATA, "No fundamentals"), ("TYPO", RefreshStatus.NO_DATA, "No fundamentals"),
    ]
    assert [row["ticker"] for row in shortlist.to_dict()["not_scored"]] == ["GONE", "TYPO"]


async def test_a_row_with_an_unreadable_timestamp_is_not_treated_as_fresh(tmp_path: Path):
    row = _extended("NVDA", 91.9, 75.3).to_dict() | {"last_updated": "yesterday-ish"}
    JsonStore(tmp_path / "scores.json").upsert("NVDA", row)
    engine = _FakeEngine(tmp_path, {"NVDA": (91.9, 75.3)})
    await score_tickers(["NVDA"], engine, tmp_path)
    assert engine.fetched == ["NVDA"]


async def test_no_more_than_three_fetches_run_at_once(tmp_path: Path, monkeypatch):
    import asyncio

    running = peak = 0

    class Slow(_FakeEngine):
        async def refresh_one(self, ticker: str, *, follow: bool = True) -> RefreshResult:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return await super().refresh_one(ticker)

    known = {f"T{i}": (50.0 + i, 50.0) for i in range(8)}
    shortlist = await score_tickers(list(known), Slow(tmp_path, known), tmp_path)
    assert len(shortlist.cards) == 8 and peak == scorecard.MAX_CONCURRENT_FETCHES


def test_a_card_carries_measured_risk_in_the_base_currency():
    from scorers.risk_profile import RiskProfile

    extended = _extended("3640.T", 94.7, 100.0)
    extended.currency, extended.market_cap, extended.fx_rate_to_base = "JPY", 1.4e10, 0.6
    extended.risk = RiskProfile(volatility_1y_pct=68.0, worst_fall_3y_pct=57.0,
                                avg_daily_traded_value=29_688_000.0, daily_turnover_pct=0.212,
                                price_history_years=9.6, fiscal_years=4, loss_years=1)
    card = Scorecard.of(extended, reused=False)
    assert card.market_cap_base == pytest.approx(8.4e9)
    assert card.traded_per_day_base == pytest.approx(29_688_000 * 0.6)
    risk = card.to_dict()["risk"]
    assert (risk["volatility_1y_pct"], risk["worst_fall_3y_pct"], risk["loss_years"]) == (68.0, 57.0, 1)
    assert risk["traded_per_day_base"] == round(29_688_000 * 0.6)

    unknown = Scorecard.of(_extended("NEW", 80.0, 60.0), reused=False).to_dict()["risk"]
    assert unknown["volatility_1y_pct"] is None and unknown["traded_per_day_base"] is None


def test_scale_is_each_company_against_the_largest_beside_it():
    big, small, unknown = (Scorecard.of(_extended(t, 80.0, 60.0), reused=False) for t in "ABC")
    big = scorecard.replace(big, market_cap_base=4_000.0)
    small = scorecard.replace(small, market_cap_base=100.0)
    scaled = scorecard._with_scale([big, small, unknown])
    assert [card.scale_vs_largest_pct for card in scaled] == [100.0, 2.5, None]
    assert scorecard._with_scale([unknown]) == [unknown]  # no size known, nothing claimed
