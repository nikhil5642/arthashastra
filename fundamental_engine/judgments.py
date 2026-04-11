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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PillarJudgment:
    score: float
    evidence: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"score": self.score, "evidence": list(self.evidence)}
        d.update(self.extras)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PillarJudgment | None":
        if not data or "score" not in data:
            return None
        score = float(data["score"])
        evidence = list(data.get("evidence") or [])
        extras = {
            k: v for k, v in data.items() if k not in {"score", "evidence"}
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

    def to_dict(self) -> dict[str, Any]:
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
    def from_dict(cls, data: dict[str, Any]) -> "Judgment":
        return cls(
            ticker=data.get("ticker", ""),
            leadership=PillarJudgment.from_dict(data.get("leadership")),
            prospects=PillarJudgment.from_dict(data.get("prospects")),
            news=PillarJudgment.from_dict(data.get("news")),
            leader_name=data.get("leader_name", ""),
            updated_at=data.get("updated_at", ""),
            judged_by=data.get("judged_by", "claude"),
        )


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
            data = json.loads(fp.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("judgment file corrupt: %s", fp)
            return None
        return Judgment.from_dict(data)

    def save(self, judgment: Judgment) -> Path:
        fp = self.path_for(judgment.ticker)
        fp.write_text(json.dumps(judgment.to_dict(), indent=2, default=str))
        return fp

    def list_tickers(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))
