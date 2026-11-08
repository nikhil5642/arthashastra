"""Persistence boundary tests — corrupt or partial JSON never crashes a reader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig, _mask
from fundamental_engine.dip_candidate import DipCandidateRecord
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.judgments import JudgmentStore, PillarJudgment
from fundamental_engine.universe import load_dip_candidates
from scorers.base import Gate, Pillar


def test_json_store_survives_corruption_and_wrong_shapes(tmp_path: Path):
    store = JsonStore(tmp_path / "scores.json")
    assert store.read() == {}
    (tmp_path / "scores.json").write_text("{broken")
    assert store.read() == {}
    (tmp_path / "scores.json").write_text("[1, 2, 3]")
    assert store.read() == {}
    store.upsert("MSFT", {"composite": 65.3})
    assert store.get("MSFT") == {"composite": 65.3}
    assert store.get("NOPE", "default") == "default"
    store.delete("MSFT")
    assert store.read() == {}


def test_config_reads_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BASE_CURRENCY", " usd ")
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    monkeypatch.setenv("BRAVE_API_KEY", "abcdefghijkl")
    cfg = AnalystConfig.from_env()
    assert cfg.data_cache_dir == tmp_path / "cache" and cfg.data_cache_dir.is_dir()
    assert cfg.base_currency == "USD"
    assert cfg.sec_user_agent == "Jane Doe jane@example.com"
    assert cfg.redacted()["brave_api_key"] == "abcd…ijkl"
    assert (_mask(""), _mask("short")) == ("(not set)", "****")

    monkeypatch.setenv("BASE_CURRENCY", "")
    monkeypatch.setenv("SEC_USER_AGENT", "")
    monkeypatch.setenv("DATA_CACHE_DIR", "relative_cache_for_test")
    cfg = AnalystConfig.from_env()
    assert cfg.base_currency == "INR"
    assert "Arthashastra" in cfg.sec_user_agent
    assert cfg.data_cache_dir.is_absolute()
    cfg.data_cache_dir.rmdir()


def test_dip_candidate_rows_are_parsed_defensively():
    good = {
        "drawdown_pct": "25.5", "severity": "ALERT", "composite_score": 80, "company_name": "Shell plc",
        "current_price": "3722.5", "divergence_bonus": True, "below_50dma": True,
    }
    record = DipCandidateRecord.from_dict("SHEL.L", good)
    assert record is not None
    assert (record.name, record.sector) == ("Shell plc", "Unknown")
    assert record.dip.drawdown_pct == 25.5 and record.dip.divergence_bonus == 1
    assert record.to_dict()["ticker"] == "SHEL.L"
    assert DipCandidateRecord.from_dict("X", {"severity": "ALERT"}) is None
    assert DipCandidateRecord.from_dict("X", {**good, "severity": "LOUD"}) is None
    assert DipCandidateRecord.from_dict("X", {**good, "company_name": ""}).name == "X"


def test_load_dip_candidates_skips_bad_rows(tmp_path: Path):
    (tmp_path / "dip_candidates.json").write_text(json.dumps({
        "NVDA": {"drawdown_pct": 22.0, "severity": "ALERT", "composite_score": 81.0, "sector": "Technology"},
        "BROKEN": "not an object",
        "PARTIAL": {"severity": "ALERT"},
    }))
    assert set(load_dip_candidates(tmp_path)) == {"NVDA"}


def test_extended_score_rejects_unusable_rows_and_cleans_fields():
    assert ExtendedScore.from_dict({"verdict": "BUY"}) is None
    assert ExtendedScore.from_dict({"composite": 70, "verdict": 3}) is None
    row = ExtendedScore.from_dict({
        "composite": "71.5", "verdict": "BUY", "ticker": 123,
        "evidence": {"fundamentals": "not a list", "unknown_pillar": ["x"], 7: ["y"]},
        "gates_applied": ["integrity_veto", "made_up_gate"],
        "fx_rate_to_base": "88.1", "current_price": "abc",
    })
    assert row is not None
    assert row.score.composite == 71.5 and row.score.ticker == "123"
    assert row.score.evidence == {Pillar.FUNDAMENTALS: []}
    assert row.score.gates_applied == [Gate.INTEGRITY_VETO]
    assert row.fx_rate_to_base == 88.1 and row.current_price is None


def test_judgment_store_rejects_corrupt_files(tmp_path: Path):
    store = JudgmentStore(tmp_path)
    store.path_for("MSFT").write_text("{not json")
    assert store.get("MSFT") is None
    store.path_for("GOOGL").write_text("[]")
    assert store.get("GOOGL") is None
    assert PillarJudgment.from_dict({"score": "eighty"}) is None
    pillar = PillarJudgment(score=70, evidence=["e"], extras={"score": 1, "mode": "PROFESSIONAL"})
    assert pillar.to_dict() == {"score": 70, "evidence": ["e"], "mode": "PROFESSIONAL"}


def test_atomic_write_cleans_up_after_a_failed_replace(tmp_path: Path, monkeypatch):
    import os

    from signals.fileio import atomic_write_text

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        atomic_write_text(tmp_path / "scores.json", "{}")
    assert list(tmp_path.iterdir()) == []


def test_dip_candidate_coercions():
    from fundamental_engine import dip_candidate as dc
    from signals.signal_bus import Severity

    assert dc._parse_severity(Severity.URGENT) is Severity.URGENT
    assert dc._parse_severity(3) is None
    assert dc._as_str(None, default="d") == "d" and dc._as_str(5, default="d") == "5"
    assert dc._as_str("", default="d") == "d"
    assert dc._as_float("x", default=1.5) == 1.5 and dc._as_float(None, default=0.0) == 0.0
    assert dc._as_float([], default=2.0) == 2.0
    assert dc._as_int("7.9", default=0) == 7 and dc._as_int("x", default=3) == 3
    assert dc._as_int(None, default=4) == 4 and dc._as_int([], default=5) == 5
