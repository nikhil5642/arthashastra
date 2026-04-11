"""Judgment cache — where Claude writes qualitative scores.

This project is Claude Code native: Python fetches real data and runs
deterministic scoring (fundamentals, dip detection), but the qualitative
pillars (leadership, prospects, news) are judged by Claude in a slash
command and written here as JSON.

When `analyse` is run from the CLI without an existing judgment, the
pipeline reports deterministic-only scoring and marks the LLM pillars as
"pending Claude judgment". Running the `/analyse-stock` skill tells Claude
to read the fetched data, form a judgment, and write it here.

Format (one file per ticker):
    data_cache/judgments/{TICKER}.json
    {
        "ticker": "RELIANCE",
        "leadership": {"score": 0-100, "mode": "FIRST_GEN|MULTI_GEN_FAMILY|PROFESSIONAL",
                       "integrity_score": 0-100, "evidence": [...], "red_flags": [...]},
        "prospects":  {"score": 0-100, "evidence": [...]},
        "news":       {"score": 0-100, "evidence": [...], "material_negatives": [...]},
        "leader_name": "...",
        "updated_at": "ISO8601",
        "judged_by": "claude"
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


_RESERVED_PILLAR_KEYS: frozenset[str] = frozenset({"score", "evidence"})


@dataclass(slots=True)
class PillarJudgment:
    score: float
    evidence: list[str] = field(default_factory=list)
    extras: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "score": self.score,
            "evidence": list(self.evidence),
        }
        # Never let extras clobber score/evidence even if a caller constructs
        # a PillarJudgment with a poisoned extras dict.
        for k, v in self.extras.items():
            if k in _RESERVED_PILLAR_KEYS:
                continue
            d[k] = v
        return d

    @classmethod
    def from_dict(
        cls, data: dict[str, object] | None
    ) -> "PillarJudgment | None":
        if not data or "score" not in data:
            return None
        score_raw = data["score"]
        if not isinstance(score_raw, (int, float)):
            return None
        score = float(score_raw)

        evidence_raw = data.get("evidence")
        evidence: list[str]
        if isinstance(evidence_raw, list):
            evidence = [str(e) for e in evidence_raw]
        else:
            evidence = []

        extras: dict[str, object] = {
            k: v for k, v in data.items() if k not in _RESERVED_PILLAR_KEYS
        }
        return cls(score=score, evidence=evidence, extras=extras)


@dataclass(slots=True)
class Judgment:
    ticker: str
    leadership: PillarJudgment | None = None
    prospects: PillarJudgment | None = None
    news: PillarJudgment | None = None
    leader_name: str = ""
    updated_at: str = ""
    judged_by: str = "claude"

    def __post_init__(self) -> None:
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "leadership": self.leadership.to_dict() if self.leadership else None,
            "prospects": self.prospects.to_dict() if self.prospects else None,
            "news": self.news.to_dict() if self.news else None,
            "leader_name": self.leader_name,
            "updated_at": self.updated_at,
            "judged_by": self.judged_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Judgment":
        return cls(
            ticker=_str_field(data.get("ticker"), default=""),
            leadership=PillarJudgment.from_dict(_dict_field(data.get("leadership"))),
            prospects=PillarJudgment.from_dict(_dict_field(data.get("prospects"))),
            news=PillarJudgment.from_dict(_dict_field(data.get("news"))),
            leader_name=_str_field(data.get("leader_name"), default=""),
            updated_at=_str_field(data.get("updated_at"), default=""),
            judged_by=_str_field(data.get("judged_by"), default="claude"),
        )


def _str_field(v: object, *, default: str) -> str:
    if v is None:
        return default
    if isinstance(v, str):
        return v
    return str(v)


def _dict_field(v: object) -> dict[str, object] | None:
    if isinstance(v, dict):
        return {str(k): val for k, val in v.items()}
    return None


class JudgmentStore:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir / "judgments"
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, ticker: str) -> Path:
        return self._dir / f"{ticker.upper()}.json"

    def get(self, ticker: str) -> Judgment | None:
        fp = self.path_for(ticker)
        if not fp.exists():
            return None
        try:
            parsed = json.loads(fp.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("judgment file corrupt: %s", fp)
            return None
        if not isinstance(parsed, dict):
            log.warning(
                "judgment file %s: top-level is %s, not a dict", fp, type(parsed).__name__
            )
            return None
        return Judgment.from_dict(parsed)

    def save(self, judgment: Judgment) -> Path:
        fp = self.path_for(judgment.ticker)
        fp.write_text(json.dumps(judgment.to_dict(), indent=2, default=str))
        return fp

    def list_tickers(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))
