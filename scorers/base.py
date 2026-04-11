"""Shared scorer types: ScorerResult, StockScore, Verdict."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Verdict = Literal["STRONG_BUY", "BUY", "HOLD", "AVOID", "RED_FLAG"]

VERDICT_BANDS: tuple[tuple[float, Verdict], ...] = (
    (80, "STRONG_BUY"),
    (70, "BUY"),
    (55, "HOLD"),
    (40, "AVOID"),
    (0, "RED_FLAG"),
)


def verdict_for(composite: float) -> Verdict:
    for threshold, v in VERDICT_BANDS:
        if composite >= threshold:
            return v
    return "RED_FLAG"


@dataclass(slots=True)
class ScorerResult:
    pillar: str
    score: float
    confidence: float
    evidence: list[str] = field(default_factory=list)
    data_completeness_pct: float = 100.0

    @property
    def low_confidence(self) -> bool:
        return self.data_completeness_pct < 60.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"low_confidence": self.low_confidence}


@dataclass(slots=True)
class StockScore:
    ticker: str
    leadership_score: float
    fundamentals_score: float
    prospects_score: float
    news_score: float
    composite: float
    verdict: Verdict
    confidence: float
    data_completeness_pct: float
    evidence: dict[str, list[str]] = field(default_factory=dict)
    last_updated: str = ""
    low_confidence: bool = False

    def __post_init__(self) -> None:
        if not self.last_updated:
            self.last_updated = datetime.now().isoformat(timespec="seconds")
        self.low_confidence = self.data_completeness_pct < 60.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
