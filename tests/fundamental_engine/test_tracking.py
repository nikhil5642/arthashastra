"""Change-tracking tests — what is recorded, and what counts as new since the last look."""

from __future__ import annotations

import json
from pathlib import Path

from data_sources.fundamentals_source import BalanceSheetSnapshot
from fundamental_engine.analyse_pipeline import score_snapshot
from fundamental_engine.config import AnalystConfig
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.tracking import changes_since, record_analysis
from scorers.base import StockScore, Verdict
from scorers.early_signals import EarlySignal, EarlySignalKind, Tone
from signals.signal_bus import Severity, SignalBus, SignalType


def _signal(kind: EarlySignalKind, tone: Tone = Tone.CONCERN,
            severity: Severity = Severity.ALERT) -> EarlySignal:
    return EarlySignal(kind=kind, tone=tone, severity=severity, message=f"{kind.value} happened")


def _row(composite: float, verdict: Verdict, *signals: EarlySignal) -> ExtendedScore:
    score = StockScore(
        ticker="GOOG", leadership_score=65, fundamentals_score=90, prospects_score=55, news_score=42,
        composite=composite, verdict=verdict, confidence=0.8, data_completeness_pct=90,
        last_updated="2026-11-09T11:40:00",
    )
    return ExtendedScore(score=score, early_signals=list(signals))


def test_a_verdict_move_and_only_the_new_material_signals_are_changes():
    debt = _signal(EarlySignalKind.DEBT_RISING)
    one_offs = _signal(EarlySignalKind.ONE_OFF_PROFIT, severity=Severity.URGENT)
    faster = _signal(EarlySignalKind.REVENUE_ACCELERATING, Tone.STRENGTH)
    goodwill = _signal(EarlySignalKind.GOODWILL_JUMP, severity=Severity.INFO)

    previous = _row(71.8, Verdict.BUY, debt)
    current = _row(66.6, Verdict.HOLD, debt, one_offs, faster, goodwill)
    changes = changes_since(previous, current)

    assert [(c.signal_type, c.severity) for c in changes] == [
        (SignalType.VERDICT_CHANGED, Severity.ALERT),
        (SignalType.EARLY_CONCERN, Severity.URGENT),
        (SignalType.EARLY_STRENGTH, Severity.ALERT),
    ]
    assert changes[0].message == "BUY → HOLD (composite 71.8 → 66.6)"
    assert changes[1].data == {"kind": "one_off_profit"}
    # Debt was already known, and an INFO signal stays in the report.
    assert all("debt_rising" not in str(c.data) and "goodwill" not in str(c.data) for c in changes)


def test_a_signal_that_got_worse_is_announced_again():
    previous = _row(66.6, Verdict.HOLD, _signal(EarlySignalKind.DEBT_RISING),
                    _signal(EarlySignalKind.GOODWILL_JUMP, severity=Severity.INFO),
                    _signal(EarlySignalKind.SHARE_DILUTION, severity=Severity.URGENT))
    current = _row(66.6, Verdict.HOLD, _signal(EarlySignalKind.DEBT_RISING, severity=Severity.URGENT),
                   _signal(EarlySignalKind.GOODWILL_JUMP),
                   _signal(EarlySignalKind.SHARE_DILUTION))
    changes = changes_since(previous, current)
    # Debt went ALERT → URGENT and goodwill INFO → ALERT; dilution eased, which is not news.
    assert [(c.data["kind"], c.severity) for c in changes] == [
        ("debt_rising", Severity.URGENT), ("goodwill_jump", Severity.ALERT),
    ]


def test_nothing_changed_means_no_changes():
    row = _row(66.6, Verdict.HOLD, _signal(EarlySignalKind.DEBT_RISING))
    assert changes_since(row, row) == []


def test_on_a_first_look_everything_material_is_new():
    current = _row(66.6, Verdict.HOLD, _signal(EarlySignalKind.DEBT_RISING),
                   _signal(EarlySignalKind.PRICE_DRAWDOWN, severity=Severity.INFO))
    changes = changes_since(None, current)
    assert [c.signal_type for c in changes] == [SignalType.EARLY_CONCERN]  # no verdict to move from


def _snapshot(**overrides: object) -> BalanceSheetSnapshot:
    fields: dict[str, object] = {
        "ticker": "GOOG", "as_of_date": "2026-11-10", "debt_to_equity": 0.14, "roe": 31.8,
        "rev_3y_cagr": 12.5, "pat_3y_cagr": 30.1, "company_name": "Alphabet Inc.",
        "sector": "Communication Services", "currency": "USD", "financial_currency": "USD",
    }
    fields.update(overrides)
    return BalanceSheetSnapshot(**fields)  # type: ignore[arg-type]


def test_record_analysis_persists_everything_and_logs_what_changed(tmp_path: Path):
    cfg = AnalystConfig(data_cache_dir=tmp_path, brave_api_key="")

    first = score_snapshot(_snapshot(), data_cache_dir=tmp_path, price_history=[("2026-11-05", 284.0)])
    recorded = record_analysis(first, cfg, fx_rate_to_base=94.1)
    assert recorded.previous is None and recorded.price_rows == 1 and recorded.changes == []
    assert json.loads((tmp_path / "scores.json").read_text())["GOOG"]["fx_rate_to_base"] == 94.1
    assert (tmp_path / "snapshots" / "GOOG.json").exists()

    # Second look: debt has jumped — a new material concern.
    second = score_snapshot(
        _snapshot(debt_change_1y_pct=321.0, latest_debt_to_equity=0.215, debt_to_equity_1y_ago=0.087),
        data_cache_dir=tmp_path,
    )
    recorded = record_analysis(second, cfg, fx_rate_to_base=94.1)
    assert recorded.previous is not None
    assert [c.signal_type for c in recorded.changes] == [SignalType.EARLY_CONCERN]

    logged = SignalBus(path=tmp_path / "signals.json").read_all()
    assert [s.signal_type for s in logged] == [
        SignalType.SCORE_UPDATED, SignalType.SCORE_UPDATED, SignalType.EARLY_CONCERN,
    ]
    assert ", +0.0 since " in logged[1].message  # the score row says how far it moved

    # Third look: the concern is still there, so it is not announced again.
    assert record_analysis(second, cfg, fx_rate_to_base=94.1).changes == []


def test_a_company_scored_in_passing_is_cached_but_not_followed(tmp_path: Path):
    """A screen scores dozens of companies; they must not flood the watchlist or its log."""
    from fundamental_engine.universe import load_scores, load_universe

    cfg = AnalystConfig(data_cache_dir=tmp_path, brave_api_key="")
    debt_jumped = dict(debt_change_1y_pct=321.0, latest_debt_to_equity=0.215, debt_to_equity_1y_ago=0.087)

    seen = score_snapshot(_snapshot(**debt_jumped), data_cache_dir=tmp_path)
    recorded = record_analysis(seen, cfg, fx_rate_to_base=94.1, follow=False)
    # The row is there for the next screen to reuse, and what it shows was noticed…
    assert not load_scores(tmp_path)["GOOG"].followed and (tmp_path / "snapshots" / "GOOG.json").exists()
    assert [c.signal_type for c in recorded.changes] == [SignalType.EARLY_CONCERN]
    # …but nothing went on the bus, and it is not part of what `refresh all` and the backtests work on.
    assert SignalBus(path=tmp_path / "signals.json").read_all() == []
    assert load_universe(tmp_path) == []

    # Asking about it by name follows it, and its concerns are news from then on.
    record_analysis(seen, cfg, fx_rate_to_base=94.1)
    assert load_scores(tmp_path)["GOOG"].followed and load_universe(tmp_path) == ["GOOG"]
    assert [s.signal_type for s in SignalBus(path=tmp_path / "signals.json").read_all()] == [
        SignalType.SCORE_UPDATED,
    ]

    # Once followed, always followed: the next screen that passes by does not undo it.
    record_analysis(seen, cfg, fx_rate_to_base=94.1, follow=False)
    assert load_scores(tmp_path)["GOOG"].followed
    assert len(SignalBus(path=tmp_path / "signals.json").read_all()) == 2


def test_rows_cached_before_the_field_existed_count_as_followed(tmp_path: Path):
    from fundamental_engine.cache import JsonStore
    from fundamental_engine.universe import load_universe

    store = JsonStore(tmp_path / "scores.json")
    store.upsert("MSFT", {"composite": 65.0, "verdict": "HOLD"})  # a legacy row
    store.upsert("TINY", {"composite": 60.0, "verdict": "HOLD", "followed": False})
    store.upsert("HALF", {})  # an aborted refresh: unparseable, but the user did ask for it
    assert load_universe(tmp_path) == ["HALF", "MSFT"]
