"""Record an analysis, and say what changed since the last look at that ticker.

`analyse`, `refresh`, `screen --score` and the engine all end the same
way: cache the score row, the full snapshot and the daily closes. This
module does that once for all of them, and — because it sees both the old
row and the new one — it is also where change is noticed:

  - VERDICT_CHANGED   the composite crossed into another band
  - EARLY_CONCERN     a concern that was not there last time, or got worse
  - EARLY_STRENGTH    a strength that was not there last time

The first look at a ticker has nothing to compare with, so everything
material it shows is new. Only ALERT / URGENT early signals go on the
bus; INFO stays in the report. A signal that is still there at the same
severity is not announced again; one that climbed (ALERT → URGENT, or
INFO → ALERT) is.

Followed or met in passing: a company asked about by name (`analyse`,
`refresh`) is followed. One that a screen or a comparison scored along
the way is cached — the next screen reuses it — but it is not followed:
nothing about it goes on the bus, and `brief`, `refresh all` and the
backtests leave it out. Asking about it by name follows it; once
followed, always followed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fundamental_engine.analyse_pipeline import AnalysisResult
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.snapshot_store import SnapshotStore
from scorers.early_signals import EarlySignal, Tone
from screener.price_history import PriceHistoryStore
from signals.signal_bus import Severity, Signal, SignalBus, SignalType

SCORES_FILENAME = "scores.json"
SIGNALS_FILENAME = "signals.json"
PRICE_DB_FILENAME = "prices.db"

_BUS_TYPE: dict[Tone, SignalType] = {
    Tone.CONCERN: SignalType.EARLY_CONCERN,
    Tone.STRENGTH: SignalType.EARLY_STRENGTH,
}


@dataclass(slots=True)
class RecordedAnalysis:
    """What `record_analysis` did, for the caller to report."""

    current: ExtendedScore
    previous: ExtendedScore | None  # the row this analysis replaced
    price_rows: int  # daily closes persisted
    changes: list[Signal] = field(default_factory=list)  # what is new since `previous`


def record_analysis(
    result: AnalysisResult,
    cfg: AnalystConfig,
    *,
    fx_rate_to_base: float | None,
    follow: bool = True,
) -> RecordedAnalysis:
    """Persist one analysis and, for a followed company, put what changed on the signal bus.

    `follow=False` is for a company scored in passing (a screen, a
    comparison); it stays followed if it already was.
    """
    ticker = result.snapshot.ticker
    scores = JsonStore(cfg.data_cache_dir / SCORES_FILENAME)
    previous = _cached_score(scores, ticker)
    current = ExtendedScore.from_analysis(result, fx_rate_to_base=fx_rate_to_base)
    current.followed = follow or (previous is not None and previous.followed)

    scores.upsert(ticker, current.to_dict())
    SnapshotStore(cfg.data_cache_dir).save(result.snapshot)
    price_rows = 0
    if result.price_history:
        with PriceHistoryStore(cfg.data_cache_dir / PRICE_DB_FILENAME) as store:
            price_rows = store.load_rows(ticker, result.price_history)

    changes = changes_since(previous, current)
    if current.followed:
        SignalBus(path=cfg.data_cache_dir / SIGNALS_FILENAME).emit_many(
            [_score_updated(previous, current), *changes]
        )
    return RecordedAnalysis(
        current=current, previous=previous, price_rows=price_rows, changes=changes
    )


def changes_since(previous: ExtendedScore | None, current: ExtendedScore) -> list[Signal]:
    """Pure: the verdict move and the material early signals that are new or more severe."""
    ticker = current.score.ticker
    changes: list[Signal] = []

    if previous is not None and previous.score.verdict != current.score.verdict:
        changes.append(
            Signal(
                signal_type=SignalType.VERDICT_CHANGED,
                severity=Severity.ALERT,
                ticker=ticker,
                message=(
                    f"{previous.score.verdict.value} → {current.score.verdict.value} "
                    f"(composite {previous.score.composite:.1f} → {current.score.composite:.1f})"
                ),
                data={
                    "from": previous.score.verdict.value,
                    "to": current.score.verdict.value,
                },
            )
        )

    # INFO is the floor: a kind never seen and a kind last seen at INFO both
    # become news the moment they turn material.
    known = (
        {signal.kind: signal.severity.rank for signal in previous.early_signals}
        if previous is not None
        else {}
    )
    changes.extend(
        _on_the_bus(ticker, signal)
        for signal in current.early_signals
        if signal.severity.rank > known.get(signal.kind, Severity.INFO.rank)
    )
    return changes


def _cached_score(scores: JsonStore, ticker: str) -> ExtendedScore | None:
    row = scores.get(ticker)
    if not isinstance(row, dict):
        return None
    return ExtendedScore.from_dict({**row, "ticker": ticker})


def _on_the_bus(ticker: str, signal: EarlySignal) -> Signal:
    return Signal(
        signal_type=_BUS_TYPE[signal.tone],
        severity=signal.severity,
        ticker=ticker,
        message=signal.message,
        data={"kind": signal.kind.value},
    )


def _score_updated(previous: ExtendedScore | None, current: ExtendedScore) -> Signal:
    score = current.score
    moved = ""
    if previous is not None:
        delta = score.composite - previous.score.composite
        moved = f", {delta:+.1f} since {previous.score.last_updated[:10] or 'the last look'}"
    return Signal(
        signal_type=SignalType.SCORE_UPDATED,
        severity=Severity.INFO,
        ticker=score.ticker,
        message=f"composite {score.composite} ({score.verdict.value}){moved}",
        data={"composite": score.composite, "verdict": score.verdict.value},
    )
