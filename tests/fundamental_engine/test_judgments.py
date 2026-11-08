"""Judgment store tests — pure I/O, no data sources."""

from __future__ import annotations

from pathlib import Path

from fundamental_engine.judgments import (
    Judgment,
    JudgmentStore,
    PillarJudgment,
)


def test_pillar_from_dict_round_trip():
    original = PillarJudgment(
        score=82.0,
        evidence=["ev1", "ev2"],
        extras={"mode": "MULTI_GEN_FAMILY"},
    )
    restored = PillarJudgment.from_dict(original.to_dict())
    assert restored is not None
    assert restored.score == 82.0
    assert restored.evidence == ["ev1", "ev2"]
    assert restored.extras["mode"] == "MULTI_GEN_FAMILY"


def test_pillar_from_dict_none():
    assert PillarJudgment.from_dict(None) is None
    assert PillarJudgment.from_dict({}) is None


def test_judgment_store_save_and_get(tmp_path: Path):
    store = JudgmentStore(tmp_path)
    judgment = Judgment(
        ticker="TEST",
        leadership=PillarJudgment(
            score=78,
            evidence=["Led major expansion"],
            extras={"mode": "PROFESSIONAL", "integrity_score": 85},
        ),
        prospects=PillarJudgment(score=72, evidence=["Strong moat"]),
        news=PillarJudgment(score=65, evidence=["Mixed Q4"]),
        leader_name="Test CEO",
    )
    fp = store.save(judgment)
    assert fp.exists()

    loaded = store.get("TEST")
    assert loaded is not None
    assert loaded.leadership is not None
    assert loaded.leadership.score == 78
    assert loaded.leadership.extras["integrity_score"] == 85
    assert loaded.prospects is not None
    assert loaded.prospects.score == 72
    assert loaded.news is not None
    assert loaded.news.score == 65
    assert loaded.leader_name == "Test CEO"


def test_judgment_store_missing_ticker(tmp_path: Path):
    store = JudgmentStore(tmp_path)
    assert store.get("NOPE") is None


def test_judgment_store_lists_tickers(tmp_path: Path):
    store = JudgmentStore(tmp_path)
    store.save(Judgment(ticker="AAA"))
    store.save(Judgment(ticker="BBB"))
    store.save(Judgment(ticker="CCC"))
    assert store.list_tickers() == ["AAA", "BBB", "CCC"]


def test_judgment_partial_pillars_round_trip(tmp_path: Path):
    store = JudgmentStore(tmp_path)
    store.save(
        Judgment(
            ticker="PART",
            leadership=PillarJudgment(score=70),
            # prospects / news intentionally None
        )
    )
    loaded = store.get("PART")
    assert loaded is not None
    assert loaded.leadership is not None
    assert loaded.prospects is None
    assert loaded.news is None


def test_integrity_veto_parsed_from_flag_or_anchor():
    by_flag = Judgment.from_dict(
        {"ticker": "X", "leadership": {"score": 25, "integrity_veto": True}}
    )
    by_anchor = Judgment.from_dict(
        {"ticker": "X", "leadership": {"score": 25, "anchors_applied": ["integrity_veto"]}}
    )
    clean = Judgment.from_dict({"ticker": "X", "leadership": {"score": 80}})
    assert by_flag.integrity_veto
    assert by_anchor.integrity_veto
    assert not clean.integrity_veto


def test_integrity_veto_round_trips_through_the_store(tmp_path: Path):
    store = JudgmentStore(tmp_path)
    store.save(
        Judgment(ticker="VETO", leadership=PillarJudgment(score=25), integrity_veto=True)
    )
    loaded = store.get("VETO")
    assert loaded is not None and loaded.integrity_veto


def test_integrity_veto_requires_a_leadership_pillar():
    assert not Judgment(ticker="X", integrity_veto=True).integrity_veto
